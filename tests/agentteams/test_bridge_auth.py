from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from apps.agentteams_bridge import operator_auth as operator_auth_module
from apps.agentteams_bridge.errors import BridgeError
from apps.agentteams_bridge.main import create_app
from apps.agentteams_bridge.models import BridgeRun, RunState
from apps.agentteams_bridge.operator_auth import BridgeOperatorAuthenticator
from apps.agentteams_bridge.settings import BridgeSettings


BRIDGE_OPERATOR_KEY = "bridge-ingress-" + ("a" * 48)
OUTBOUND_EGO_OPERATOR_KEY = "ego-outbound-" + ("b" * 48)


def _run() -> BridgeRun:
    return BridgeRun(
        id="atrun_auth_test",
        ego_task_id="task-auth-test",
        agentteams_project_id="project-auth-test",
        team="ego-researchops",
        trace_id="trace_auth_test",
        correlation_id="corr_auth_test",
        context_version=1,
        state=RunState.PRE_APPROVAL,
        mode="dry_run",
        objective="Verify bridge ingress authentication without live side effects",
        task_graph=[],
        checkpoint={},
        ack_timeout_seconds=5,
        execution_timeout_seconds=30,
        max_reassignments=0,
    )


class _FakeBridge:
    def __init__(self) -> None:
        self.run = _run()
        self.start_calls = 0
        self.forbidden_mutation_calls = 0

    def probe_live(self, _team: str) -> dict[str, Any]:
        return {"live": True}

    def start_run(self, _body: Any) -> BridgeRun:
        self.start_calls += 1
        return self.run

    def get_run(self, _run_id: str) -> BridgeRun:
        return self.run

    def reconcile(self, _run_id: str) -> Any:
        self.forbidden_mutation_calls += 1
        raise AssertionError("unauthenticated reconcile reached the service")

    def grant_r2(self, _run_id: str, _body: Any) -> Any:
        self.forbidden_mutation_calls += 1
        raise AssertionError("unauthenticated grant reached the service")

    def recover_active(self) -> Any:
        self.forbidden_mutation_calls += 1
        raise AssertionError("unauthenticated recovery reached the service")

    def events(self, _run_id: str) -> dict[str, Any]:
        return {"items": [], "total": 0, "chain_valid": True}

    def receipts(self, _run_id: str) -> dict[str, Any]:
        return {"items": [], "total": 0, "chain_valid": True}

    def index(self) -> dict[str, Any]:
        return {"items": [], "total": 0}

    def skill_evidence(self, _run_id: str) -> dict[str, Any]:
        return {"items": [], "total": 0}


def _start_body() -> dict[str, Any]:
    return {
        "ego_task_id": "task-auth-test",
        "objective": "Verify authenticated bridge control-plane ingress",
        "trace_id": "trace_auth_test",
        "correlation_id": "corr_auth_test",
        "ack_timeout_seconds": 5,
        "execution_timeout_seconds": 30,
        "max_reassignments": 0,
        "mode": "dry_run",
    }


def test_bridge_mutations_fail_closed_and_reads_remain_public() -> None:
    service = _FakeBridge()
    application = create_app(
        service,  # type: ignore[arg-type]
        operator_key="",
        outbound_ego_operator_key=OUTBOUND_EGO_OPERATOR_KEY,
    )
    with TestClient(application) as client:
        health = client.get("/api/v1/agentteams/health")
        assert health.status_code == 200
        assert health.json()["operator_auth"] == {
            "configured": False,
            "scheme": "Bearer",
            "mutations_fail_closed": True,
        }
        assert client.get("/api/v1/agentteams/runs/auth-test").status_code == 200

        blocked = client.post("/api/v1/agentteams/runs", json=_start_body())
        assert blocked.status_code == 503
        assert blocked.json()["error"]["code"] == ("bridge_operator_auth_not_configured")
        assert service.start_calls == 0


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/agentteams/runs/auth-test/reconcile", None),
        (
            "/api/v1/agentteams/runs/auth-test/r2-grant",
            {
                "approval_token": "approval-token-auth-test",
                "idempotency_key": "auth-test-grant",
            },
        ),
        ("/api/v1/agentteams/recover", None),
    ],
)
def test_every_bridge_mutation_authenticates_before_dispatch(
    path: str, body: dict[str, Any] | None
) -> None:
    service = _FakeBridge()
    application = create_app(
        service,  # type: ignore[arg-type]
        operator_key=BRIDGE_OPERATOR_KEY,
        outbound_ego_operator_key=OUTBOUND_EGO_OPERATOR_KEY,
    )
    with TestClient(application) as client:
        response = client.post(path, json=body)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["error"]["code"] == "bridge_operator_auth_required"
    assert service.forbidden_mutation_calls == 0


def test_bridge_operator_key_rejects_wrong_and_accepts_correct_bearer() -> None:
    service = _FakeBridge()
    application = create_app(
        service,  # type: ignore[arg-type]
        operator_key=BRIDGE_OPERATOR_KEY,
        outbound_ego_operator_key=OUTBOUND_EGO_OPERATOR_KEY,
    )
    with TestClient(application) as client:
        malformed = client.post("/api/v1/agentteams/runs", json={})
        assert malformed.status_code == 401
        assert malformed.json()["error"]["code"] == "bridge_operator_auth_required"

        wrong = client.post(
            "/api/v1/agentteams/runs",
            json=_start_body(),
            headers={"Authorization": "Bearer %s" % ("x" * 64)},
        )
        assert wrong.status_code == 403
        assert wrong.json()["error"]["code"] == "bridge_operator_auth_invalid"
        assert service.start_calls == 0

        allowed = client.post(
            "/api/v1/agentteams/runs",
            json=_start_body(),
            headers={"Authorization": "Bearer %s" % BRIDGE_OPERATOR_KEY},
        )
        assert allowed.status_code == 201, allowed.text
        assert allowed.json()["run"]["id"] == service.run.id
        assert service.start_calls == 1


def test_bridge_operator_key_is_independent_and_compared_in_constant_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="between 32 and 4096"):
        BridgeOperatorAuthenticator(
            "too-short", outbound_ego_operator_key=OUTBOUND_EGO_OPERATOR_KEY
        )
    with pytest.raises(ValueError, match="independent from EGO_OPERATOR_KEY"):
        BridgeOperatorAuthenticator(
            BRIDGE_OPERATOR_KEY,
            outbound_ego_operator_key=BRIDGE_OPERATOR_KEY,
        )
    assert BRIDGE_OPERATOR_KEY not in repr(BridgeSettings(bridge_operator_key=BRIDGE_OPERATOR_KEY))

    authenticator = BridgeOperatorAuthenticator(
        BRIDGE_OPERATOR_KEY,
        outbound_ego_operator_key="",
    )
    actual_compare = operator_auth_module.hmac.compare_digest
    calls: list[tuple[bytes, bytes]] = []

    def recording_compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return actual_compare(left, right)

    monkeypatch.setattr(operator_auth_module.hmac, "compare_digest", recording_compare)
    with pytest.raises(BridgeError) as caught:
        authenticator.authenticate("Bearer %s" % ("y" * 64))
    assert caught.value.code == "bridge_operator_auth_invalid"
    assert len(calls) == 1
    assert len(calls[0][0]) == len(calls[0][1]) == 32
