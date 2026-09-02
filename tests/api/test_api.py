import io
import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from tests.api.operator_auth_helpers import (
    TEST_AUTHORIZATION_HEADERS,
    TEST_OPERATOR_ID,
    TEST_OPERATOR_KEY,
)


TASK_ID = "ego-lite-001"


def _pause_for_approval(client: TestClient):
    response = client.post("/api/v1/tasks/%s/autorun" % TASK_ID, json={})
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "paused"
    assert payload["paused_reason"] == "human_approval_required"
    assert payload["task"]["stage"] == "APPROVAL"
    approval = payload["task"]["pending_approval"]
    assert approval["action"] == "gpu.launch_experiment"
    assert approval["config_sha256"] == approval["action_payload"]["config_sha256"]
    assert approval["rollback_point"].startswith("Restore the baseline-ltx")
    return approval


def _approve(client: TestClient, approval):
    response = client.post(
        "/api/v1/approvals/%s/decision" % approval["id"],
        json={
            "decision": "approved",
            "approver": TEST_OPERATOR_ID,
            "expected_digest": approval["action_digest"],
        },
        headers={"Idempotency-Key": "approve-demo-0001"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["approval_token"]
    assert token.startswith("egoap_")
    return token


def test_health_cors_and_truthful_integrations(client: TestClient) -> None:
    health = client.get("/api/v1/health")
    assert health.status_code == 200
    assert health.json()["database"]["status"] == "ready"
    assert health.json()["mode"] == "deterministic-local"

    integrations = client.get("/api/v1/integrations").json()
    assert integrations["mode"] == "verified_handshake_or_metadata"
    assert "ready" not in {item["status"] for item in integrations["items"]}

    preflight = client.options(
        "/api/v1/tasks",
        headers={
            "Origin": "http://localhost:4173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert preflight.status_code == 200
    assert preflight.headers["access-control-allow-origin"] == "http://localhost:4173"


def test_agentteams_integration_requires_a_live_bridge_handshake(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    payload = {
        "live": True,
        "team": {
            "name": "ego-researchops",
            "phase": "Active",
            "leaderReady": True,
            "readyWorkers": 3,
            "totalWorkers": 3,
        },
    }
    monkeypatch.setenv("EGO_HICLAW_URL", "http://127.0.0.1:18090/bridge-health")
    monkeypatch.setattr(
        "apps.api.service.DIRECT_HTTP.open",
        lambda *_args, **_kwargs: Response(json.dumps(payload).encode()),
    )

    integrations = client.get("/api/v1/integrations").json()
    agentteams = next(item for item in integrations["items"] if item["id"] == "hiclaw")
    assert agentteams["status"] == "ready"
    assert "leaderReady=True" in agentteams["detail"]


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"live": True, "team": []},
        {"live": False, "team": {"name": "ego-researchops"}},
    ],
)
def test_agentteams_integration_fails_closed_on_invalid_bridge_payload(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    class Response(io.BytesIO):
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    monkeypatch.setenv("EGO_HICLAW_URL", "http://127.0.0.1:18090/bridge-health")
    monkeypatch.setattr(
        "apps.api.service.DIRECT_HTTP.open",
        lambda *_args, **_kwargs: Response(json.dumps(payload).encode()),
    )

    integrations = client.get("/api/v1/integrations").json()
    agentteams = next(item for item in integrations["items"] if item["id"] == "hiclaw")
    assert agentteams["status"] == "unavailable"
    assert "ready" not in {item["status"] for item in integrations["items"]}


def test_agentteams_integration_is_unavailable_when_bridge_cannot_be_reached(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unreachable(*_args, **_kwargs):
        raise OSError("offline")

    monkeypatch.setenv("EGO_HICLAW_URL", "http://127.0.0.1:18090/bridge-health")
    monkeypatch.setattr(
        "apps.api.service.DIRECT_HTTP.open",
        unreachable,
    )

    integrations = client.get("/api/v1/integrations").json()
    agentteams = next(item for item in integrations["items"] if item["id"] == "hiclaw")
    assert agentteams["status"] == "unavailable"
    assert agentteams["endpoint_configured"] is True


def test_e2e_happy_path_pauses_for_human_then_completes(client: TestClient) -> None:
    reset = client.post("/api/v1/demo/reset", json={})
    assert reset.status_code == 200
    assert reset.json()["task"]["data_notice"].startswith("SYNTHETIC DEMO DATA")

    approval = _pause_for_approval(client)

    bypass = client.post("/api/v1/tasks/%s/advance" % TASK_ID, json={})
    assert bypass.status_code == 403
    assert bypass.json()["error"]["code"] == "approval_required"

    wrong_digest = client.post(
        "/api/v1/approvals/%s/decision" % approval["id"],
        json={
            "decision": "approved",
            "approver": TEST_OPERATOR_ID,
            "expected_digest": "0" * 64,
        },
    )
    assert wrong_digest.status_code == 403
    assert wrong_digest.json()["error"]["code"] == "approval_digest_mismatch"

    token = _approve(client, approval)
    completed = client.post(
        "/api/v1/tasks/%s/autorun" % TASK_ID,
        json={"approval_token": token},
    )
    assert completed.status_code == 200, completed.text
    payload = completed.json()
    assert payload["status"] == "completed"
    assert payload["task"]["stage"] == "COMPLETED"
    assert payload["task"]["decision"] == "KEEP"
    assert payload["task"]["gate_result"]["status"] == "pass"
    assert payload["task"]["evidence_summary"]["missing"] == []
    assert len(payload["task"]["memory_candidates"]) == 2
    assert all(
        item["status"] == "candidate" and item["proposed_by"] == "memory-agent"
        for item in payload["task"]["memory_candidates"]
    )
    assert len(payload["task"]["memories"]) == 2
    assert all(item["validated"] for item in payload["task"]["memories"])
    assert all(item["candidate_id"] for item in payload["task"]["memories"])
    assert all(
        item["validated_by"] == "memory-validator" for item in payload["task"]["memories"]
    )
    assert all(
        result["data_classification"] == "synthetic_demo"
        for result in payload["task"]["latest_evaluation"]
    )

    event_payload = client.get("/api/v1/tasks/%s/events" % TASK_ID).json()
    assert event_payload["append_only"] is True
    assert event_payload["chain_valid"] is True
    events = event_payload["events"]
    assert len(events) > 10
    plan_reviews = [event for event in events if event["event_type"] == "plan.review.passed"]
    assert len(plan_reviews) == 1
    assert plan_reviews[0]["actor"] == "reviewer-agent"
    assert plan_reviews[0]["payload"]["independent"] is True
    candidate_events = [
        event for event in events if event["event_type"] == "memory.candidate.proposed"
    ]
    validation_events = [event for event in events if event["event_type"] == "memory.validated"]
    assert len(candidate_events) == len(validation_events) == 2
    assert {event["actor"] for event in candidate_events} == {"memory-agent"}
    assert {event["actor"] for event in validation_events} == {"memory-validator"}
    assert events[0]["previous_hash"] == "0" * 64
    for previous, current in zip(events, events[1:]):
        assert current["previous_hash"] == previous["event_hash"]


def test_approval_decision_is_idempotent_and_token_is_still_single_use(client: TestClient) -> None:
    client.post("/api/v1/demo/reset", json={})
    approval = _pause_for_approval(client)
    request = {
        "decision": "approved",
        "approver": TEST_OPERATOR_ID,
        "expected_digest": approval["action_digest"],
    }
    first = client.post(
        "/api/v1/approvals/%s/decision" % approval["id"],
        json=request,
        headers={"Idempotency-Key": "same-approval-key"},
    )
    second = client.post(
        "/api/v1/approvals/%s/decision" % approval["id"],
        json=request,
        headers={"Idempotency-Key": "same-approval-key"},
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["approval_token"].startswith("egoap_")
    assert second.json().get("approval_token") is None
    assert second.json()["idempotent_replay"] is True

    token = first.json()["approval_token"]
    crossed = client.post("/api/v1/tasks/%s/advance" % TASK_ID, json={"approval_token": token})
    assert crossed.status_code == 200
    assert crossed.json()["task"]["stage"] == "EXECUTE"

    replay = client.post(
        "/api/v1/tasks/%s/advance" % TASK_ID,
        json={"target": "EXECUTE", "approval_token": token},
    )
    assert replay.status_code == 409
    assert replay.json()["error"]["code"] == "illegal_transition"


def test_approval_idempotency_cache_never_persists_plaintext_token(client: TestClient) -> None:
    # The fixture database path is recoverable from the service and intentionally inspected here:
    # a one-time bearer secret must never enter the replay cache.
    client.post("/api/v1/demo/reset", json={})
    approval = _pause_for_approval(client)
    request = {
        "decision": "approved",
        "approver": TEST_OPERATOR_ID,
        "expected_digest": approval["action_digest"],
    }
    first = client.post(
        "/api/v1/approvals/%s/decision" % approval["id"],
        json=request,
        headers={"Idempotency-Key": "approval-secret-audit"},
    )
    assert first.status_code == 200
    raw_token = first.json()["approval_token"]

    database = client.app.state.service.store.db_path
    with sqlite3.connect(database) as connection:
        cached = connection.execute(
            "SELECT response_json FROM idempotency WHERE key=?",
            ("approval-secret-audit",),
        ).fetchone()
        logical_database = "\n".join(connection.iterdump())
    assert cached is not None
    assert raw_token not in cached[0]
    assert "egoap_" not in cached[0]
    assert raw_token not in logical_database


def test_reentering_approval_after_denial_issues_a_fresh_record(client: TestClient) -> None:
    client.post("/api/v1/demo/reset", json={})
    original = _pause_for_approval(client)
    denied = client.post(
        "/api/v1/approvals/%s/decision" % original["id"],
        json={
            "decision": "denied",
            "approver": TEST_OPERATOR_ID,
            "expected_digest": original["action_digest"],
        },
    )
    assert denied.status_code == 200
    assert denied.json()["approval"]["status"] == "denied"

    assert client.post(
        "/api/v1/tasks/%s/advance" % TASK_ID, json={"target": "PLAN"}
    ).status_code == 200
    assert client.post(
        "/api/v1/tasks/%s/advance" % TASK_ID, json={"target": "PLAN_REVIEW"}
    ).status_code == 200
    reentered = client.post(
        "/api/v1/tasks/%s/advance" % TASK_ID, json={"target": "APPROVAL"}
    )
    assert reentered.status_code == 200, reentered.text
    fresh = reentered.json()["task"]["pending_approval"]
    assert fresh["id"] != original["id"]
    assert fresh["status"] == "pending"


def test_reentering_approval_after_verify_issues_a_fresh_record(client: TestClient) -> None:
    client.post("/api/v1/demo/reset", json={})
    original = _pause_for_approval(client)
    token = _approve(client, original)
    assert client.post(
        "/api/v1/tasks/%s/advance" % TASK_ID,
        json={"target": "EXECUTE", "approval_token": token},
    ).status_code == 200
    for target in ("OBSERVE", "EVALUATE", "VERIFY", "PLAN", "PLAN_REVIEW", "APPROVAL"):
        response = client.post(
            "/api/v1/tasks/%s/advance" % TASK_ID,
            json={"target": target},
        )
        assert response.status_code == 200, response.text
    fresh = response.json()["task"]["pending_approval"]
    assert fresh["id"] != original["id"]
    assert fresh["status"] == "pending"


def test_e2e_insufficient_evidence_branch_stops_before_decision(client: TestClient) -> None:
    reset = client.post("/api/v1/demo/reset", json={"scenario": "insufficient_evidence"})
    assert reset.status_code == 200
    approval = _pause_for_approval(client)
    token = _approve(client, approval)

    blocked = client.post("/api/v1/tasks/%s/autorun" % TASK_ID, json={"approval_token": token})
    assert blocked.status_code == 200, blocked.text
    payload = blocked.json()
    assert payload["status"] == "paused"
    assert payload["paused_reason"] == "insufficient_evidence"
    assert payload["task"]["stage"] == "VERIFY"
    assert payload["gate"]["status"] == "fail"
    assert "trace" in payload["task"]["evidence_summary"]["missing"]
    assert payload["task"]["decision"] is None

    forced = client.post("/api/v1/tasks/%s/advance" % TASK_ID, json={"target": "DECIDE"})
    assert forced.status_code == 409
    assert forced.json()["error"]["code"] == "evidence_gate_failed"


def test_structured_validation_and_not_found_errors(client: TestClient) -> None:
    invalid = client.post("/api/v1/demo/reset", json={"scenario": "made-up", "unexpected": True})
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "invalid_request"
    assert invalid.json()["error"]["request_id"].startswith("req_")

    missing = client.get("/api/v1/tasks/not-real")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "not_found"


def test_idempotency_key_reuse_with_different_body_is_rejected(client: TestClient) -> None:
    headers = {"Idempotency-Key": "reset-scenario-key"}
    first = client.post("/api/v1/demo/reset", json={"scenario": "happy_path"}, headers=headers)
    assert first.status_code == 200
    conflict = client.post(
        "/api/v1/demo/reset",
        json={"scenario": "insufficient_evidence"},
        headers=headers,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_conflict"


def test_sqlite_survives_restart_and_audit_rows_are_immutable(tmp_path: Path) -> None:
    database = tmp_path / "persistent.sqlite3"
    with TestClient(
        create_app(
            str(database),
            operator_key=TEST_OPERATOR_KEY,
            operator_id=TEST_OPERATOR_ID,
        )
    ) as first_client:
        first_client.headers.update(TEST_AUTHORIZATION_HEADERS)
        advanced = first_client.post(
            "/api/v1/tasks/%s/advance" % TASK_ID, json={"target": "CONTEXT"}
        )
        assert advanced.status_code == 200
        generation = advanced.json()["task"]["generation"]

    with TestClient(
        create_app(
            str(database),
            operator_key=TEST_OPERATOR_KEY,
            operator_id=TEST_OPERATOR_ID,
        )
    ) as restarted_client:
        persisted = restarted_client.get("/api/v1/tasks/%s" % TASK_ID)
        assert persisted.status_code == 200
        assert persisted.json()["stage"] == "CONTEXT"
        assert persisted.json()["generation"] == generation
        assert (
            restarted_client.get("/api/v1/tasks/%s/events" % TASK_ID).json()["chain_valid"] is True
        )

    connection = sqlite3.connect(str(database))
    try:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            connection.execute("UPDATE audit_events SET actor='tampered' WHERE sequence=1")
    finally:
        connection.close()


def test_dashboard_activity_is_scoped_to_active_generation(client: TestClient) -> None:
    first = client.post("/api/v1/demo/reset", json={}).json()["task"]
    client.post("/api/v1/tasks/%s/advance" % TASK_ID, json={"target": "CONTEXT"})
    second = client.post("/api/v1/demo/reset", json={}).json()["task"]
    assert first["generation"] != second["generation"]

    dashboard = client.get("/api/v1/dashboard").json()
    activity = dashboard["activity"]
    assert dashboard["demo"]["generation"] == second["generation"]
    assert activity
    assert {event["generation"] for event in activity} == {second["generation"]}
    assert [event["sequence"] for event in activity] == sorted(
        event["sequence"] for event in activity
    )
