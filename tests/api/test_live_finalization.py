from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, List

from fastapi.testclient import TestClient

from apps.api.evaluator import evaluate_paired_metric
from apps.api.main import APPROVAL_TOKEN_HEADER
from apps.api.provenance import canonical_sha256
from tests.api.operator_auth_helpers import TEST_OPERATOR_ID


TASK_ID = "live-agentteams-001"
CONFIG_SHA = "c" * 64


def _create_body() -> Dict[str, Any]:
    return {
        "task_id": TASK_ID,
        "title": "Bounded live AgentTeams evaluation",
        "objective": "Run one approved real evaluation and freeze its evidence chain",
        "synthetic": False,
        "risk_level": "R2",
        "goal": {
            "objective": "Compare one frozen candidate against one frozen baseline",
            "frozen": True,
            "hardware": "one externally provisioned GPU",
            "constraints": {
                "gpu_count": 1,
                "wall_time_seconds": 900,
                "seed": 42,
            },
            "acceptance_metrics": [
                {
                    "name": "score",
                    "direction": "higher_better",
                    "threshold": 1.5,
                    "unit": "points",
                    "rule": "candidate mean >= 1.5",
                }
            ],
            "candidate_arms": [
                {"id": "baseline", "name": "Baseline", "description": "Frozen baseline"},
                {"id": "candidate", "name": "Candidate", "description": "Frozen candidate"},
            ],
        },
        "live_source": {
            "source": "agentteams",
            "team": "ego-researchops",
            "trace_id": "trace-live-finalization",
            "correlation_id": "corr-live-finalization",
            "context_version": 1,
        },
        "execution_contract": {
            "action": "gpu.launch_experiment",
            "config_sha256": CONFIG_SHA,
            "action_payload": {
                "config_sha256": CONFIG_SHA,
                "entrypoint": "eval_pose",
                "gpu_ids": [0],
                "seed": 42,
                "synthetic": False,
            },
            "rollback_point": "Preserve the frozen baseline and terminate the bounded job",
            "approval_ttl_seconds": 900,
        },
        "owner_agent": "research-pi",
    }


def _create_and_execute(client: TestClient) -> Dict[str, Any]:
    created = client.post(
        "/api/v1/tasks",
        json=_create_body(),
        headers={"Idempotency-Key": "create-live-task-001"},
    )
    assert created.status_code == 201, created.text
    task = created.json()["task"]
    for target in ("CONTEXT", "PLAN", "PLAN_REVIEW", "APPROVAL"):
        advanced = client.post(
            "/api/v1/tasks/%s/advance" % TASK_ID,
            json={"target": target},
            headers={"Idempotency-Key": "advance-live-%s" % target.lower()},
        )
        assert advanced.status_code == 200, advanced.text
        task = advanced.json()["task"]
    approval = task["pending_approval"]
    assert approval["action_payload"]["synthetic"] is False
    assert approval["config_sha256"] == CONFIG_SHA
    decided = client.post(
        "/api/v1/approvals/%s/decision" % approval["id"],
        json={
            "decision": "approved",
            "approver": TEST_OPERATOR_ID,
            "expected_digest": approval["action_digest"],
        },
        headers={"Idempotency-Key": "approve-live-task-001"},
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["approval_token"] is None
    assert decided.headers["cache-control"] == "no-store"
    approval_token = decided.headers[APPROVAL_TOKEN_HEADER]
    assert approval_token.startswith("egoap_")
    assert approval_token not in decided.text
    replayed_decision = client.post(
        "/api/v1/approvals/%s/decision" % approval["id"],
        json={
            "decision": "approved",
            "approver": TEST_OPERATOR_ID,
            "expected_digest": approval["action_digest"],
        },
        headers={"Idempotency-Key": "approve-live-task-001"},
    )
    assert replayed_decision.status_code == 200
    assert replayed_decision.json()["approval_token"] is None
    assert replayed_decision.json()["idempotent_replay"] is True
    assert APPROVAL_TOKEN_HEADER not in replayed_decision.headers
    executed = client.post(
        "/api/v1/tasks/%s/advance" % TASK_ID,
        json={"target": "EXECUTE", "approval_token": approval_token},
        headers={"Idempotency-Key": "execute-live-task-001"},
    )
    assert executed.status_code == 200, executed.text
    return executed.json()["task"]


def _receipt(source: str, operation: str, identifier: str) -> Dict[str, Any]:
    return {
        "source": source,
        "operation": operation,
        "method": "GET" if source == "agentteams" else "POST",
        "endpoint": "/official/%s" % operation,
        "http_status": 200,
        "request_sha256": canonical_sha256({"operation": operation}),
        "response_sha256": canonical_sha256({"identifier": identifier}),
        "response_identifier": identifier,
    }


def _artifact(uri: str, content: str) -> Dict[str, Any]:
    return {
        "uri": uri,
        "media_type": "application/json",
        "content_sha256": canonical_sha256(content),
        "size_bytes": len(content.encode("utf-8")),
    }


def _item(
    generation: str,
    kind: str,
    producer: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "generation": generation,
        "kind": kind,
        "producer_id": producer,
        "artifact_digest": canonical_sha256(payload),
        "payload": payload,
        "synthetic": False,
    }


def _evidence(generation: str) -> List[Dict[str, Any]]:
    definitions = [
        ("dataset_manifest", "CONTEXT", "ego-scout"),
        ("config", "PLAN", "ego-architect"),
        ("code", "EXECUTE", "ego-runtime"),
        ("log", "OBSERVE", "ego-runtime"),
        ("trace", "OBSERVE", "ego-runtime"),
    ]
    items: List[Dict[str, Any]] = []
    for kind, stage, producer in definitions:
        payload = {
            "schema": "egoagentos.external-artifact-evidence/v1",
            "stage": stage,
            "artifact": _artifact("agentteams://%s/output.json" % kind, kind),
            "receipts": [
                _receipt("agentteams", "task-artifact-%s" % kind, "official-%s" % kind)
            ],
            "attributes": {"kind": kind, "project_id": "official-project-001"},
            "synthetic": False,
        }
        items.append(_item(generation, kind, producer, payload))

    raw_samples = {"score": {"baseline": [1.0, 1.1], "candidate": [2.0, 2.1]}}
    result = evaluate_paired_metric(
        "score",
        raw_samples["score"]["baseline"],
        raw_samples["score"]["candidate"],
        "higher_better",
        1.5,
        seed=42,
        iterations=100,
        data_classification="external_live",
    )
    metric_payload = {
        "schema": "egoagentos.external-metric-evidence/v1",
        "stage": "EVALUATE",
        "artifact": _artifact("agentteams://metric/raw.json", "raw-metric"),
        "receipts": [
            _receipt("agentteams", "task-artifact-metric", "official-metric"),
            _receipt("gpu", "gpu-job", "gpu-job-001"),
        ],
        "evaluator": "paired_bootstrap/v1",
        "evaluator_sha256": "e" * 64,
        "deterministic": True,
        "summary_only": False,
        "raw_samples": raw_samples,
        "raw_metric_digest": canonical_sha256(raw_samples),
        "results": [result.model_dump(mode="json")],
        "attributes": {"scheduler_job_id": "gpu-job-001"},
        "synthetic": False,
    }
    items.append(_item(generation, "metric", "ego-evaluator", metric_payload))

    reviewed = [item["artifact_digest"] for item in items]
    review_payload = {
        "schema": "egoagentos.external-review-evidence/v1",
        "stage": "VERIFY",
        "artifact": _artifact("agentteams://review/decision.json", "review-pass"),
        "receipts": [
            _receipt("agentteams", "task-artifact-review", "official-review")
        ],
        "reviewer_id": "ego-reviewer",
        "reviewed_producers": [
            "ego-scout",
            "ego-architect",
            "ego-runtime",
            "ego-evaluator",
        ],
        "independent": True,
        "verdict": "PASS",
        "reviewed_evidence_digests": reviewed,
        "findings": [],
        "attributes": {"decision": "accept evidence set"},
        "synthetic": False,
    }
    items.append(_item(generation, "review", "ego-reviewer", review_payload))
    return items


def test_live_task_creation_requires_explicit_false_and_never_overwrites(
    client: TestClient,
) -> None:
    missing = _create_body()
    missing.pop("synthetic")
    assert client.post("/api/v1/tasks", json=missing).status_code == 422

    synthetic = _create_body()
    synthetic["synthetic"] = True
    assert client.post("/api/v1/tasks", json=synthetic).status_code == 422

    first = client.post(
        "/api/v1/tasks",
        json=_create_body(),
        headers={"Idempotency-Key": "create-live-contract"},
    )
    replay = client.post(
        "/api/v1/tasks",
        json=_create_body(),
        headers={"Idempotency-Key": "create-live-contract"},
    )
    assert first.status_code == replay.status_code == 201
    assert first.json()["task"]["synthetic_demo"] is False
    assert replay.json()["idempotent_replay"] is True

    collision = client.post(
        "/api/v1/tasks",
        json=_create_body(),
        headers={"Idempotency-Key": "create-live-collision"},
    )
    assert collision.status_code == 409
    assert collision.json()["error"]["code"] == "task_already_exists"


def test_live_task_cannot_autorun_or_advance_without_typed_evidence(client: TestClient) -> None:
    task = _create_and_execute(client)
    autorun = client.post("/api/v1/tasks/%s/autorun" % TASK_ID, json={})
    assert autorun.status_code == 403
    assert autorun.json()["error"]["code"] == "live_autorun_forbidden"

    bypass = client.post(
        "/api/v1/tasks/%s/advance" % TASK_ID,
        json={"target": "OBSERVE"},
    )
    assert bypass.status_code == 409
    assert bypass.json()["error"]["code"] == "live_stage_evidence_missing"
    assert client.get("/api/v1/tasks/%s" % TASK_ID).json()["version"] == task["version"]


def test_live_finalization_recomputes_metrics_gates_and_completes_idempotently(
    client: TestClient,
) -> None:
    task = _create_and_execute(client)
    body = {
        "generation": task["generation"],
        "expected_task_version": task["version"],
        "evidence": _evidence(task["generation"]),
        "terminal_actor": "research-pi",
    }
    first = client.post(
        "/api/v1/tasks/%s/finalize" % TASK_ID,
        json=body,
        headers={"Idempotency-Key": "finalize-live-task"},
    )
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["task"]["stage"] == "COMPLETED"
    assert payload["task"]["decision"] == "KEEP"
    assert payload["task"]["gate_result"]["status"] == "pass"
    assert payload["receipt"]["synthetic"] is False
    assert payload["receipt"]["contract_gate_status"] == "pass"
    assert payload["receipt"]["verification_status"] == "CONTRACT_PASS_ORIGIN_UNVERIFIED"
    assert payload["receipt"]["external_origin_status"] == "UNVERIFIED"
    assert payload["receipt"]["live_claim_allowed"] is False
    assert payload["task"]["verification"]["live_claim_allowed"] is False
    assert set(payload["receipt"]["evidence_digests"]) == {
        "code",
        "config",
        "dataset_manifest",
        "log",
        "metric",
        "trace",
        "review",
    }
    assert all(record["synthetic"] is False for record in payload["task"]["evidence"])
    assert [transition["to"] for transition in payload["transitions"]] == [
        "OBSERVE",
        "EVALUATE",
        "VERIFY",
        "DECIDE",
        "ARCHIVE",
        "MEMORY_SKILL",
        "COMPLETED",
    ]

    replay = client.post(
        "/api/v1/tasks/%s/finalize" % TASK_ID,
        json=body,
        headers={"Idempotency-Key": "finalize-live-task"},
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert client.get("/api/v1/tasks/%s/events" % TASK_ID).json()["chain_valid"] is True


def test_live_finalization_failures_are_atomic_and_do_not_fabricate_progress(
    client: TestClient,
) -> None:
    task = _create_and_execute(client)
    incomplete = _evidence(task["generation"])[0:6]
    failed = client.post(
        "/api/v1/tasks/%s/finalize" % TASK_ID,
        json={
            "generation": task["generation"],
            "expected_task_version": task["version"],
            "evidence": incomplete,
            "terminal_actor": "research-pi",
        },
    )
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "terminal_evidence_incomplete"
    unchanged = client.get("/api/v1/tasks/%s" % TASK_ID).json()
    assert unchanged["stage"] == "EXECUTE"
    assert unchanged["evidence"] == []

    tampered = _evidence(task["generation"])
    metric = next(item for item in tampered if item["kind"] == "metric")
    metric["payload"]["results"][0]["candidate_mean"] = 999.0
    metric["artifact_digest"] = canonical_sha256(metric["payload"])
    rejected = client.post(
        "/api/v1/tasks/%s/finalize" % TASK_ID,
        json={
            "generation": task["generation"],
            "expected_task_version": task["version"],
            "evidence": tampered,
            "terminal_actor": "research-pi",
        },
    )
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "metric_recompute_mismatch"
    assert client.get("/api/v1/tasks/%s" % TASK_ID).json()["evidence"] == []

    forged = _evidence(task["generation"])
    review = next(item for item in forged if item["kind"] == "review")
    review["producer_id"] = "ego-runtime"
    review["payload"]["reviewer_id"] = "ego-runtime"
    review["artifact_digest"] = canonical_sha256(review["payload"])
    forged_response = client.post(
        "/api/v1/tasks/%s/finalize" % TASK_ID,
        json={
            "generation": task["generation"],
            "expected_task_version": task["version"],
            "evidence": forged,
            "terminal_actor": "research-pi",
        },
    )
    assert forged_response.status_code == 409
    assert forged_response.json()["error"]["code"] == "terminal_evidence_gate_failed"
    assert client.get("/api/v1/tasks/%s" % TASK_ID).json()["evidence"] == []


def test_live_evidence_digest_and_synthetic_demo_are_fail_closed(client: TestClient) -> None:
    task = _create_and_execute(client)
    item = deepcopy(_evidence(task["generation"])[0])
    item["artifact_digest"] = "0" * 64
    mismatch = client.post(
        "/api/v1/tasks/%s/evidence" % TASK_ID,
        json={"expected_task_version": task["version"], "evidence": item},
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "evidence_digest_mismatch"

    demo = client.get("/api/v1/tasks/ego-lite-001").json()
    demo_item = _evidence(demo["generation"])[0]
    synthetic_rejected = client.post(
        "/api/v1/tasks/ego-lite-001/evidence",
        json={"expected_task_version": demo["version"], "evidence": demo_item},
    )
    assert synthetic_rejected.status_code == 403
    assert synthetic_rejected.json()["error"]["code"] == "live_evidence_on_synthetic_task"
