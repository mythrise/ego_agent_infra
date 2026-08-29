from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from apps.agentteams_bridge.models import (
    CollaborationEnvelope,
    EnvelopeKind,
    StartRunRequest,
)
from benchmarks.trace_verifier import SCENARIO_REQUIRED_EVENTS
from integrations.agentteams.benchmark_adapter import (
    CANONICAL_SCENARIO_EVENTS,
    run_scenario,
)


ROOT = Path(__file__).resolve().parents[2]


def test_official_contract_pin_and_resource_shape() -> None:
    lock = json.loads(
        (ROOT / "integrations/agentteams/official-contract.lock.json").read_text()
    )
    assert lock["stable"] == {
        "tag": "v1.2.2",
        "commit": "849182af8e017168a5a200a87b1062142caf462d",
        "note": "Latest stable release shown by the official README during implementation.",
    }
    assert lock["main"]["commit"] == "223ddc2b8073e4c8b93bcbb15e1d717f196c04d9"
    assert lock["main"]["required_for_live_bridge"] is True
    assert lock["apiVersion"] == "agentteams.io/v1beta1"
    assert len(lock["artifacts"]) == 7

    resources = (ROOT / "integrations/agentteams/agentteams-resources.yaml.tmpl").read_text()
    assert resources.count("kind: Worker") == 7
    assert "kind: Team" in resources
    assert "kind: Manager" in resources
    assert "name: ego-research-lead\n      role: team_leader" in resources
    assert "kind: Team" in resources and resources.index("kind: Team") > resources.rindex("kind: Worker")


def test_fixture_files_cannot_be_mistaken_for_live_responses() -> None:
    for path in sorted((ROOT / "tests/agentteams/fixtures").glob("*.fixture.json")):
        payload = json.loads(path.read_text())
        assert "CONTRACT FIXTURE ONLY" in payload["_fixture_notice"]


def test_trace_and_result_schemas_encode_the_truth_gates() -> None:
    trace = json.loads(
        (
            ROOT / "benchmarks/schemas/agentteams-rxp-trace-v1.schema.json"
        ).read_text()
    )
    assert trace["properties"]["source"] == {"const": "AgentTeams"}
    assert trace["properties"]["execution_mode"] == {"const": "real-agentteams"}
    assert trace["properties"]["external_origin_status"] == {"const": "UNVERIFIED"}
    assert trace["properties"]["agents"]["minItems"] == 3
    assert {"events", "rxp", "principals", "replay"} <= set(
        trace["required"]
    )
    assert CANONICAL_SCENARIO_EVENTS == {
        key: set(value) for key, value in SCENARIO_REQUIRED_EVENTS.items()
    }
    bridge_chain = trace["properties"]["bridge_event_chain"]
    assert {
        "hash_algorithm",
        "external_origin_status",
        "source_ledger_total",
        "items",
    } <= set(bridge_chain["required"])
    assert bridge_chain["properties"]["items"]["items"]["additionalProperties"] is False

    result = json.loads(
        (ROOT / "integrations/agentteams/result-envelope.schema.json").read_text()
    )
    assert {"review_verdict", "independent_review"} <= set(result["required"])


def test_collaboration_envelope_binds_body_digest() -> None:
    envelope = CollaborationEnvelope.build(
        task_id="task-123",
        project_id="project-123",
        trace_id="trace-123456",
        correlation_id="corr-123456",
        context_version=2,
        kind=EnvelopeKind.TASK_REQUEST,
        sender="egoagentos-bridge",
        recipient="agentteams-team-leader",
        body={"command": "delegate", "version": 2},
    )
    assert envelope.body_sha256
    changed = envelope.model_dump(mode="json", by_alias=True)
    changed["body"] = {"command": "bypass"}
    with pytest.raises(ValidationError, match="body_sha256"):
        CollaborationEnvelope.model_validate(changed)


def test_dry_run_makes_zero_upstream_calls_and_never_claims_live(bridge, fake_transport) -> None:
    run = bridge.start_run(
        StartRunRequest(
            ego_task_id="task-dry",
            objective="Generate a dry-run orchestration plan only",
            mode="dry_run",
        )
    )
    assert run.mode == "dry_run"
    assert run.checkpoint["truth"] == "DRY_RUN_ONLY"
    assert run.checkpoint["live"] is False
    assert fake_transport.calls == []


def test_benchmark_without_live_opt_in_is_skip(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("AGENTTEAMS_BENCHMARK_LIVE", raising=False)
    result = run_scenario({"ego_task_id": "task", "objective": "objective"}, 7, tmp_path)
    assert result["status"] == "skip"
    assert result["details"]["execution_mode"] != "real-agentteams"
    assert not list(tmp_path.iterdir())


def test_benchmark_live_opt_in_is_unimplemented_skip_without_side_effects(
    monkeypatch, tmp_path
) -> None:
    monkeypatch.setenv("AGENTTEAMS_BENCHMARK_LIVE", "1")

    def unexpected_service_start():
        raise AssertionError("unimplemented benchmark must not start the live bridge")

    monkeypatch.setattr(
        "integrations.agentteams.benchmark_adapter.build_service",
        unexpected_service_start,
    )
    result = run_scenario(
        {
            "id": "worker_timeout_reassign",
            "ego_task_id": "real-task",
            "objective": "inject a timeout",
            "approval_token": "must-not-be-consumed",
        },
        11,
        tmp_path,
    )

    assert result["status"] == "skip"
    assert result["details"]["capability_status"] == "UNIMPLEMENTED"
    assert result["details"]["execution_mode"] == (
        "agentteams-live-target-unimplemented"
    )
    assert result["details"]["scenario_id"] == "worker_timeout_reassign"
    assert "fresh replay harness" in result["details"]["reason"]
    assert not list(tmp_path.iterdir())
