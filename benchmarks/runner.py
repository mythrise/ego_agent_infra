"""CLI runner for the versioned RXP benchmark corpus."""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Type

from benchmarks import BENCHMARK_VERSION
from benchmarks.evidence_bundle import persist_evidence_bundle, replay_evidence_bundle
from benchmarks.model import (
    Observation,
    Scenario,
    canonical_json,
    canonical_sha256,
    derive_seed,
    load_corpus,
)
from benchmarks.oracle import adjudicate
from benchmarks.profiles import (
    AgentTeamsRXPProfile,
    DeterministicCoreProfile,
    ScriptedNegativeControlProfile,
)
from benchmarks.profiles.base import Profile
from benchmarks.report import render_markdown
from benchmarks.statistics import summarize


PROFILE_TYPES: Dict[str, Type[Profile]] = {
    ScriptedNegativeControlProfile.name: ScriptedNegativeControlProfile,
    DeterministicCoreProfile.name: DeterministicCoreProfile,
    AgentTeamsRXPProfile.name: AgentTeamsRXPProfile,
}


def _git(args: Sequence[str]) -> str:
    try:
        return subprocess.check_output(
            ["git"] + list(args), text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def environment_metadata() -> Dict[str, Any]:
    dirty = _git(["status", "--porcelain"])
    return {
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "python": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "git_commit": _git(["rev-parse", "HEAD"]),
        "git_dirty": dirty not in {"", "unknown"},
        "cpu_count": os.cpu_count(),
        "gpu": "not requested / not used",
        "execution_class": "local synthetic CPU-only control-plane benchmark",
    }


def _semantic_projection(observations: List[Observation]) -> List[Dict[str, Any]]:
    excluded = {"latency_ms", "mttr_ms", "details"}
    return [
        {key: value for key, value in item.to_dict().items() if key not in excluded}
        for item in observations
    ]


def run_benchmark(
    profiles: List[Profile],
    repetitions: int,
    master_seed: int,
    evidence_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    if repetitions < 1:
        raise ValueError("repetitions must be at least 1")
    corpus = load_corpus()
    scenario_by_id = {scenario.id: scenario for scenario in corpus.scenarios}
    if evidence_dir is not None:
        evidence_dir = evidence_dir.resolve()
        if evidence_dir.exists() and any(evidence_dir.iterdir()):
            raise ValueError("evidence directory must be new or empty")
        evidence_dir.mkdir(parents=True, exist_ok=True)
    observations: List[Observation] = []
    with tempfile.TemporaryDirectory(prefix="rxp-bench-") as temporary:
        temp_root = Path(temporary)
        for profile in profiles:
            for scenario in corpus.scenarios:
                for repetition in range(repetitions):
                    seed = derive_seed(master_seed, scenario.id, repetition)
                    trial_dir = temp_root / profile.name / scenario.id / str(repetition)
                    trial_dir.mkdir(parents=True, exist_ok=True)
                    raw_observation = profile.run(scenario, seed, repetition, trial_dir)
                    observation = adjudicate(scenario, raw_observation)
                    if evidence_dir is not None and observation.trace_root:
                        persist_evidence_bundle(
                            observation,
                            scenario=scenario_by_id[observation.scenario_id],
                            seed=seed,
                            trial_workspace=trial_dir,
                            evidence_dir=evidence_dir,
                        )
                    observations.append(observation)
    summary = summarize(observations)
    semantic_projection = _semantic_projection(observations)
    return {
        "schema_version": "rxp-bench-result/v1",
        "benchmark": BENCHMARK_VERSION,
        "corpus_version": corpus.corpus_version,
        "corpus_digest": corpus.digest,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "environment": environment_metadata(),
        "configuration": {
            "master_seed": master_seed,
            "repetitions": repetitions,
            "profile_order": [profile.name for profile in profiles],
            "canonical_json": "UTF-8, sorted keys, compact separators, NaN forbidden",
            "evidence_persistence": {
                "enabled": evidence_dir is not None,
                "root": str(evidence_dir) if evidence_dir is not None else None,
                "layout": "<profile>/<scenario>/repetition-NNN/{manifest.json,trace.json}",
            },
        },
        "scenarios": [asdict(scenario) for scenario in corpus.scenarios],
        "trials": [observation.to_dict() for observation in observations],
        "summary": summary,
        "semantic_digest": canonical_sha256(semantic_projection),
    }


def strict_failures(result: Dict[str, Any]) -> List[str]:
    """Development gate for the locally implemented deterministic core.

    This deliberately permits target-profile skips so contributors without an
    AgentTeams deployment can run CI.  Use ``release_gate_failures`` for a
    semifinal claim or release artifact.
    """

    failures = []
    core = result["summary"]["profiles"].get(DeterministicCoreProfile.name)
    if core:
        if core["errors"]:
            failures.append("deterministic core produced %d errors" % core["errors"])
        if core["scenario_success"]["successes"] != core["scenario_success"]["n"]:
            failures.append("one or more executed deterministic-core scenarios failed")
        bypass = core["approval_bypass_success"]
        if bypass["successes"] != 0:
            failures.append("approval bypass succeeded %d times" % bypass["successes"])
    return failures


def release_gate_failures(
    result: Dict[str, Any],
    profile_name: str = AgentTeamsRXPProfile.name,
    evidence_dir: Optional[Path] = None,
) -> List[str]:
    """Fail closed unless the nominated release profile proves the full corpus.

    Unlike ``--strict``, this gate treats every skip and error as a failure and
    checks the scenario-level safety signals used in the semifinal scorecard.
    """

    failures: List[str] = []
    summary = result["summary"]["profiles"].get(profile_name)
    if summary is None:
        return ["release profile %s was not executed" % profile_name]

    if evidence_dir is None:
        failures.append("release gate requires a persistent --evidence-dir")
    else:
        evidence_dir = evidence_dir.resolve()

    expected_scenarios = {item["id"] for item in result["scenarios"]}
    repetitions = int(result["configuration"]["repetitions"])
    expected_trials = len(expected_scenarios) * repetitions
    if summary["trials"] != expected_trials:
        failures.append(
            "release profile produced %d/%d expected trials"
            % (summary["trials"], expected_trials)
        )
    for status in ("skipped", "errors"):
        if summary[status]:
            failures.append("release profile has %d %s trials" % (summary[status], status))
    success = summary["scenario_success"]
    expected_scenario_clusters = len(expected_scenarios)
    if (
        success["n"] != expected_scenario_clusters
        or success["successes"] != expected_scenario_clusters
    ):
        failures.append(
            "release profile passed %d/%d required scenario clusters"
            % (success["successes"], expected_scenario_clusters)
        )

    status_by_scenario = summary.get("scenario_status", {})
    missing_scenarios = expected_scenarios - set(status_by_scenario)
    if missing_scenarios:
        failures.append("release profile omitted scenarios: %s" % sorted(missing_scenarios))
    for scenario_id in sorted(expected_scenarios & set(status_by_scenario)):
        counts = status_by_scenario[scenario_id]
        if counts.get("pass", 0) != repetitions or any(
            counts.get(status, 0) for status in ("fail", "skip", "error")
        ):
            failures.append("scenario %s is not %d/%d PASS" % (scenario_id, repetitions, repetitions))

    target_trials = [
        item for item in result["trials"] if item.get("profile") == profile_name
    ]
    unsafe_scenarios = {
        "plan_conflict",
        "stale_context",
        "evidence_tamper",
        "forged_reviewer",
        "matrix_cherry_pick",
        "matrix_missing_seed",
    }
    approval_scenarios = {"token_replay", "token_expiry", "token_scope_mismatch"}
    exactly_once_scenarios = {"worker_timeout_reassign", "concurrent_duplicate"}
    recovery_scenarios = {
        "worker_timeout_reassign",
        "crash_recovery",
        "skill_version_rollback",
    }
    dynamic_scenarios = {"plan_conflict", "worker_timeout_reassign"}
    for trial in target_trials:
        label = "%s repetition %s" % (trial["scenario_id"], trial["repetition"])
        if trial.get("status") != "pass":
            continue
        if trial.get("trace_completeness") != 1.0:
            failures.append("%s lacks a complete correlated trace" % label)
        if trial.get("evidence_completeness") != 1.0:
            failures.append("%s lacks a complete evidence bundle" % label)
        if trial.get("reproducible") is not True:
            failures.append("%s did not prove deterministic replay" % label)
        if trial.get("hash_agreement") is not True:
            failures.append("%s did not prove cross-check hash agreement" % label)
        if not trial.get("trace_root"):
            failures.append("%s lacks a content-addressed trace root" % label)
        if not trial.get("evidence_root"):
            failures.append("%s lacks a content-addressed evidence root" % label)
        scenario_id = trial["scenario_id"]
        if scenario_id == "happy_path" and trial.get("task_completed") is not True:
            failures.append("%s did not complete" % label)
        if scenario_id in unsafe_scenarios and trial.get("unsafe_action_blocked") is not True:
            failures.append("%s did not block the unsafe action" % label)
        if scenario_id in approval_scenarios and trial.get("approval_bypass_succeeded") is not False:
            failures.append("%s did not prove zero approval bypass" % label)
        if scenario_id in exactly_once_scenarios and trial.get("exactly_once") is not True:
            failures.append("%s did not prove exactly-once effects" % label)
        if scenario_id in recovery_scenarios and trial.get("recovered") is not True:
            failures.append("%s did not prove recovery" % label)
        if scenario_id in dynamic_scenarios and trial.get("dynamically_routed") is not True:
            failures.append("%s did not prove dynamic routing" % label)
        if evidence_dir is not None:
            bundle = (
                evidence_dir
                / profile_name
                / scenario_id
                / ("repetition-%03d" % int(trial["repetition"]))
            )
            scenario_data = next(
                item for item in result["scenarios"] if item["id"] == scenario_id
            )
            try:
                replay_evidence_bundle(
                    bundle,
                    scenario=Scenario(**scenario_data),
                    observation=trial,
                )
            except (OSError, ValueError, KeyError, StopIteration) as error:
                failures.append("%s evidence replay failed: %s" % (label, str(error)))
    return failures


def _parse_profiles(value: str) -> List[Profile]:
    names = list(PROFILE_TYPES) if value == "all" else [item.strip() for item in value.split(",")]
    unknown = [name for name in names if name not in PROFILE_TYPES]
    if unknown:
        raise ValueError("unknown profiles: %s" % ", ".join(unknown))
    return [PROFILE_TYPES[name]() for name in names]


def main(argv: Sequence[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profiles", default="all", help="all or comma-separated profile names")
    parser.add_argument("--repetitions", type=int, default=None)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--output-json", type=Path, default=Path("benchmarks/artifacts/latest.json"))
    parser.add_argument("--output-md", type=Path, default=Path("benchmarks/artifacts/latest.md"))
    parser.add_argument(
        "--evidence-dir",
        type=Path,
        help="new or empty directory for persistent target trace bundles",
    )
    parser.add_argument("--strict", action="store_true")
    parser.add_argument(
        "--release-gate",
        metavar="PROFILE",
        help=(
            "fail closed unless PROFILE passes every corpus trial and all semifinal safety "
            "invariants; intended for release evidence, not contributor CI"
        ),
    )
    args = parser.parse_args(list(argv) if argv else None)
    corpus = load_corpus()
    repetitions = corpus.default_repetitions if args.repetitions is None else args.repetitions
    master_seed = corpus.master_seed if args.seed is None else args.seed
    try:
        profiles = _parse_profiles(args.profiles)
        result = run_benchmark(
            profiles,
            repetitions,
            master_seed,
            evidence_dir=args.evidence_dir,
        )
    except ValueError as error:
        parser.error(str(error))
        return 2
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_md.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(canonical_json(result) + "\n", encoding="utf-8")
    args.output_md.write_text(render_markdown(result), encoding="utf-8")
    print("Wrote %s" % args.output_json)
    print("Wrote %s" % args.output_md)
    print("Semantic digest: %s" % result["semantic_digest"])
    failures = strict_failures(result) if args.strict else []
    if args.release_gate:
        failures.extend(
            release_gate_failures(
                result,
                args.release_gate,
                evidence_dir=args.evidence_dir,
            )
        )
    for failure in failures:
        print("GATE FAILURE: %s" % failure, file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
