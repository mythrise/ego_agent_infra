import json
from pathlib import Path
from typing import Any, Dict

import pytest

from benchmarks.model import Observation, canonical_json, canonical_sha256, derive_seed, load_corpus
from benchmarks.oracle import adjudicate
from benchmarks.profiles import (
    AgentTeamsRXPProfile,
    DeterministicCoreProfile,
    ScriptedNegativeControlProfile,
)
from benchmarks.profiles.base import Profile
from benchmarks.report import render_markdown
from benchmarks.runner import (
    _semantic_projection,
    release_gate_failures,
    run_benchmark,
    strict_failures,
)
from benchmarks.trace_verifier import TraceValidationError, verify_trace_bytes
from tests.benchmarks.trace_fixture import build_trace, trace_bytes


class VerifiedFixtureProfile(Profile):
    """Test-only in-process producer; never presented as real AgentTeams evidence."""

    name = "agentteams-rxp-target"
    description = "test-only verified trace fixture"

    def run(
        self,
        scenario: Any,
        seed: int,
        repetition: int,
        workspace: Path,
    ) -> Observation:
        payload = trace_bytes(scenario, seed)
        trace_path = workspace / "trace.json"
        trace_path.write_bytes(payload)
        verified = verify_trace_bytes(payload, scenario=scenario, seed=seed)
        facts = verified.facts
        return Observation(
            profile=self.name,
            scenario_id=scenario.id,
            repetition=repetition,
            seed=seed,
            status="pass",
            latency_ms=1.0,
            operation_count=int(facts["operation_count"]),
            task_completed=facts["task_completed"],
            unsafe_action_blocked=facts["unsafe_action_blocked"],
            approval_bypass_succeeded=facts["approval_bypass_succeeded"],
            exactly_once=facts["exactly_once"],
            trace_completeness=facts["trace_completeness"],
            evidence_completeness=facts["evidence_completeness"],
            recovered=facts["recovered"],
            reproducible=facts["reproducible"],
            hash_agreement=facts["hash_agreement"],
            dynamically_routed=facts["dynamically_routed"],
            trace_root=verified.trace_root,
            evidence_root=verified.evidence_root,
            implementation_path="tests.benchmarks.VerifiedFixtureProfile",
            details={
                "execution_mode": "test-fixture",
                "verified_trace_path": trace_path.name,
                "action_effect_count": facts["action_effect_count"],
            },
        )


def test_corpus_is_versioned_unique_and_seeded() -> None:
    corpus = load_corpus()
    assert corpus.benchmark == "rxp-bench/v1"
    assert corpus.corpus_version == "1.0.0"
    assert len(corpus.scenarios) == 14
    assert len({scenario.id for scenario in corpus.scenarios}) == 14
    assert derive_seed(corpus.master_seed, "happy_path", 0) == derive_seed(
        corpus.master_seed, "happy_path", 0
    )
    assert derive_seed(corpus.master_seed, "happy_path", 0) != derive_seed(
        corpus.master_seed, "happy_path", 1
    )


def test_profiles_match_golden_control_outcomes() -> None:
    result = run_benchmark(
        [ScriptedNegativeControlProfile(), DeterministicCoreProfile()], 1, 20260829
    )
    actual = {
        profile: {
            scenario: next(status for status, count in counts.items() if count == 1)
            for scenario, counts in summary["scenario_status"].items()
        }
        for profile, summary in result["summary"]["profiles"].items()
    }
    golden = json.loads(
        (Path(__file__).parent / "golden" / "expected-status-v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert actual == golden
    assert strict_failures(result) == []


def test_scripted_negative_control_does_not_claim_replay_metrics() -> None:
    result = run_benchmark([ScriptedNegativeControlProfile()], 1, 7)
    summary = result["summary"]["profiles"]["scripted-negative-control-v1"]
    assert summary["reproducibility"]["n"] == 0
    assert summary["hash_agreement"]["n"] == 0
    assert all(trial["reproducible"] is None for trial in result["trials"])
    assert all(trial["hash_agreement"] is None for trial in result["trials"])


def test_deterministic_core_has_zero_approval_bypass() -> None:
    result = run_benchmark([DeterministicCoreProfile()], 2, 20260829)
    core = result["summary"]["profiles"]["deterministic-core-v0.1"]
    assert core["approval_bypass_success"]["successes"] == 0
    assert core["approval_bypass_success"]["n"] == 4
    assert core["approval_bypass_success"]["trial_n"] == 8
    assert core["exactly_once"]["value"] == 1.0
    assert core["scenario_success"]["value"] == 1.0
    assert core["coverage"] == 10 / 14


def test_canonical_output_and_markdown_report() -> None:
    result = run_benchmark([ScriptedNegativeControlProfile()], 1, 7)
    encoded = canonical_json(result)
    assert canonical_json(json.loads(encoded)) == encoded
    assert len(canonical_sha256(result)) == 64
    report = render_markdown(result)
    assert "RXP Bench report" in report
    assert "P / F / E / S" in report
    assert "not measured" in report
    assert result["semantic_digest"] in report


def _happy() -> Any:
    return next(item for item in load_corpus().scenarios if item.id == "happy_path")


def test_schema_aware_verifier_accepts_complete_trace() -> None:
    scenario = _happy()
    verified = verify_trace_bytes(trace_bytes(scenario, 17), scenario=scenario, seed=17)
    assert verified.trace_root.startswith("sha256:")
    assert verified.evidence_root.startswith("sha256:")
    assert verified.agent_roles == ("planner", "reviewer", "runtime")
    assert verified.facts["task_completed"] is True
    assert verified.facts["exactly_once"] is True
    assert verified.facts["hash_agreement"] is True
    assert verified.facts["bridge_event_chain_total"] == 5


def _tamper_bridge_sequence(trace: Dict[str, Any]) -> None:
    trace["bridge_event_chain"]["items"][1]["sequence"] = 99


def _tamper_bridge_previous_hash(trace: Dict[str, Any]) -> None:
    trace["bridge_event_chain"]["items"][1]["previous_hash"] = "f" * 64


def _tamper_bridge_event_hash(trace: Dict[str, Any]) -> None:
    trace["bridge_event_chain"]["items"][1]["event_hash"] = "f" * 64


def _tamper_bridge_envelope(trace: Dict[str, Any]) -> None:
    trace["bridge_event_chain"]["items"][1]["envelope"]["recipient"] = "forged-worker"


def _tamper_bridge_created_at(trace: Dict[str, Any]) -> None:
    item = trace["bridge_event_chain"]["items"][1]
    item["created_at"] = "2026-08-29T01:02:03+00:00"
    item["envelope"]["created_at"] = item["created_at"]


def _tamper_bridge_head(trace: Dict[str, Any]) -> None:
    trace["bridge_event_chain"]["head"] = "f" * 64


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (_tamper_bridge_sequence, "sequence is not contiguous"),
        (_tamper_bridge_previous_hash, "previous_hash mismatch"),
        (_tamper_bridge_event_hash, "event_hash mismatch"),
        (_tamper_bridge_envelope, "event_hash mismatch"),
        (_tamper_bridge_created_at, "event_hash mismatch"),
        (_tamper_bridge_head, "head mismatch"),
    ],
    ids=["sequence", "previous-hash", "event-hash", "envelope", "created-at", "head"],
)
def test_bridge_ledger_chain_is_recomputed_instead_of_trusting_valid(
    mutation: Any, message: str
) -> None:
    scenario = _happy()
    trace = build_trace(scenario, 18)
    assert trace["bridge_event_chain"]["valid"] is True
    mutation(trace)
    payload = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with pytest.raises(TraceValidationError, match=message):
        verify_trace_bytes(payload, scenario=scenario, seed=18)


@pytest.mark.parametrize("location", ["trace", "chain"])
def test_external_agentteams_origin_cannot_be_promoted_by_adapter_claim(
    location: str,
) -> None:
    scenario = _happy()
    trace = build_trace(scenario, 18)
    if location == "trace":
        trace["external_origin_status"] = "VERIFIED"
    else:
        trace["bridge_event_chain"]["external_origin_status"] = "VERIFIED"
    payload = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with pytest.raises(TraceValidationError, match="external origin must remain UNVERIFIED"):
        verify_trace_bytes(payload, scenario=scenario, seed=18)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda trace: trace.update(events=[]), "events must be non-empty"),
        (
            lambda trace: trace["agents"][1].update(role="runtime"),
            "three distinct AgentTeams roles",
        ),
        (
            lambda trace: next(
                event
                for event in trace["events"]
                if event["type"] == "independent_review.passed"
            ).update(actor="worker-executor"),
            "reviewer must be independent",
        ),
        (
            lambda trace: trace["events"][2].update(correlation_id="forged-correlation"),
            "share the trial task/correlation ids",
        ),
    ],
)
def test_schema_aware_verifier_rejects_empty_or_forged_traces(
    mutation: Any, message: str
) -> None:
    scenario = _happy()
    trace = build_trace(scenario, 19)
    mutation(trace)
    payload = json.dumps(trace, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with pytest.raises(TraceValidationError, match=message):
        verify_trace_bytes(payload, scenario=scenario, seed=19)


def test_profile_pass_requires_digest_bound_schema_verified_trace(tmp_path: Path) -> None:
    scenario = _happy()
    payload = trace_bytes(scenario, 23)
    trace = tmp_path / "trace.json"
    trace.write_bytes(payload)
    raw: Dict[str, Any] = {
        "status": "pass",
        "details": {
            "execution_mode": "real-agentteams",
            "synthetic": False,
            "agent_roles": ["runtime", "reviewer", "planner"],
            "agentteams_trace_path": trace.name,
            "trace_sha256": canonical_sha256(json.loads(payload)),
        },
    }
    verified, _ = AgentTeamsRXPProfile._verified_pass(raw, tmp_path, scenario, 23)
    assert verified.trace_sha256 == raw["details"]["trace_sha256"]
    raw["details"]["trace_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="digest"):
        AgentTeamsRXPProfile._verified_pass(raw, tmp_path, scenario, 23)


def test_adapter_timeout_is_an_error(tmp_path: Path) -> None:
    adapter = tmp_path / "hanging_adapter.py"
    adapter.write_text(
        "BENCHMARK_ADAPTER_VERSION = 'rxp-bench/v1'\n"
        "import time\n"
        "def run_scenario(scenario, seed, workspace):\n"
        "    time.sleep(5)\n"
        "    return {'status': 'skip'}\n",
        encoding="utf-8",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    observation = AgentTeamsRXPProfile(
        adapter=str(adapter), timeout_seconds=0.05
    ).run(_happy(), 3, 0, workspace)
    assert observation.status == "error"
    assert observation.reason is not None and "TimeoutError" in observation.reason


def test_module_adapter_discovery_is_independent_of_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = tmp_path / "fixture_adapter"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "adapter.py").write_text(
        "BENCHMARK_ADAPTER_VERSION = 'rxp-bench/v1'\n"
        "def run_scenario(scenario, seed, workspace):\n"
        "    return {'status': 'skip', 'reason': 'fixture skip'}\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setenv("PYTHONPATH", str(tmp_path))
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)
    workspace = tmp_path / "trial"
    workspace.mkdir()
    observation = AgentTeamsRXPProfile(adapter="fixture_adapter.adapter").run(
        _happy(), 5, 0, workspace
    )
    assert observation.status == "skip"
    assert observation.reason == "fixture skip"


def test_trace_and_evidence_roots_change_the_semantic_digest() -> None:
    scenario = _happy()
    first = verify_trace_bytes(trace_bytes(scenario, 29), scenario=scenario, seed=29)
    changed = build_trace(scenario, 29)
    changed["truth_boundary"] += " changed"
    changed_payload = json.dumps(changed, sort_keys=True, separators=(",", ":")).encode()
    second = verify_trace_bytes(changed_payload, scenario=scenario, seed=29)
    assert first.trace_root != second.trace_root
    assert first.evidence_root != second.evidence_root
    base = Observation(
        profile="target",
        scenario_id="happy_path",
        repetition=0,
        seed=29,
        status="pass",
        latency_ms=1.0,
        operation_count=1,
        trace_root=first.trace_root,
        evidence_root=first.evidence_root,
    )
    changed_observation = Observation(
        **{
            **base.to_dict(),
            "trace_root": second.trace_root,
            "evidence_root": second.evidence_root,
        }
    )
    assert canonical_sha256(_semantic_projection([base])) != canonical_sha256(
        _semantic_projection([changed_observation])
    )


def test_release_gate_requires_persisted_replayable_evidence(tmp_path: Path) -> None:
    evidence_dir = tmp_path / "evidence"
    result = run_benchmark(
        [VerifiedFixtureProfile()], 1, 20260829, evidence_dir=evidence_dir
    )
    assert release_gate_failures(
        result, evidence_dir=evidence_dir
    ) == []
    assert any("persistent --evidence-dir" in item for item in release_gate_failures(result))
    persisted_trace = (
        evidence_dir
        / "agentteams-rxp-target"
        / "happy_path"
        / "repetition-000"
        / "trace.json"
    )
    persisted_trace.write_bytes(persisted_trace.read_bytes() + b" ")
    failures = release_gate_failures(result, evidence_dir=evidence_dir)
    assert any("evidence replay failed" in item for item in failures)


def test_release_gate_fails_closed_on_target_skips(tmp_path: Path) -> None:
    result = run_benchmark(
        [AgentTeamsRXPProfile()], 1, 20260829, evidence_dir=tmp_path / "evidence"
    )
    failures = release_gate_failures(result, evidence_dir=tmp_path / "evidence")
    assert failures
    assert any("skipped" in failure for failure in failures)
    assert any("passed 0/14" in failure for failure in failures)


def test_live_opt_in_is_an_explicit_unimplemented_skip_not_adapter_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENTTEAMS_BENCHMARK_LIVE", "1")

    observation = AgentTeamsRXPProfile().run(_happy(), 7, 0, tmp_path)

    assert observation.status == "skip"
    assert observation.reason is not None and "not implemented" in observation.reason
    assert observation.details["capability_status"] == "UNIMPLEMENTED"
    assert observation.details["execution_mode"] == (
        "agentteams-live-target-unimplemented"
    )
    assert not list(tmp_path.glob("agentteams-live-trace.json"))


def test_development_strict_gate_does_not_mislabel_negative_control_as_safe() -> None:
    result = run_benchmark([ScriptedNegativeControlProfile()], 1, 20260829)
    assert strict_failures(result) == []
    assert release_gate_failures(result) == [
        "release profile agentteams-rxp-target was not executed"
    ]


def test_oracle_downgrades_self_reported_happy_path_without_safety() -> None:
    scenario = _happy()
    raw = ScriptedNegativeControlProfile().run(scenario, 7, 0, Path("."))
    assert raw.status == "pass"
    judged = adjudicate(scenario, raw)
    assert judged.status == "fail"
    assert judged.reason is not None and judged.reason.startswith("oracle:")


def test_errors_and_skips_have_explicit_denominators(tmp_path: Path) -> None:
    result = run_benchmark(
        [AgentTeamsRXPProfile()], 1, 20260829, evidence_dir=tmp_path / "evidence"
    )
    summary = result["summary"]["profiles"]["agentteams-rxp-target"]
    assert summary["scenario_success"]["n"] == 0
    assert summary["coverage"] == 0.0
    assert summary["denominators"] == {
        "all_trials": 14,
        "executed_including_errors": 0,
        "measured_excluding_errors_and_skips": 0,
        "scenario_clusters_with_attempts": 0,
    }


def test_zero_repetitions_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        run_benchmark([ScriptedNegativeControlProfile()], 0, 20260829)
