from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Optional

import pytest
from fastapi.testclient import TestClient

from apps.agentteams_bridge.clients import EgoClient
from apps.agentteams_bridge.settings import BridgeSettings
from apps.agentteams_bridge.transport import HTTPResponse
from apps.api import operator_auth as operator_auth_module
from apps.api.errors import ControlPlaneError
from apps.api.main import create_app
from apps.api.operator_auth import OperatorAuthenticator
from tests.api.operator_auth_helpers import (
    TEST_AUTHORIZATION_HEADERS,
    TEST_OPERATOR_ID,
    TEST_OPERATOR_KEY,
)


def _pause_demo_for_approval(client: TestClient) -> dict[str, Any]:
    client.post("/api/v1/demo/reset", json={}).raise_for_status()
    paused = client.post("/api/v1/tasks/ego-lite-001/autorun", json={})
    assert paused.status_code == 200, paused.text
    return paused.json()["task"]["pending_approval"]


def test_unconfigured_mutations_fail_closed_but_read_endpoints_remain_public(
    tmp_path: Path,
) -> None:
    application = create_app(
        str(tmp_path / "unconfigured.sqlite3"),
        operator_key="",
        operator_id=TEST_OPERATOR_ID,
        allow_unauthenticated_demo=False,
    )
    with TestClient(application) as client:
        health = client.get("/api/v1/health")
        assert health.status_code == 200
        assert health.json()["operator_auth"] == {
            "configured": False,
            "scheme": "Bearer",
            "operator_id": None,
            "unauthenticated_demo_enabled": False,
            "live_mutations_fail_closed": True,
        }
        assert client.get("/api/v1/dashboard").status_code == 200

        preflight = client.options(
            "/api/v1/tasks",
            headers={
                "Origin": "http://localhost:4173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization",
            },
        )
        assert preflight.status_code == 200
        assert "Authorization" in preflight.headers["access-control-allow-headers"]

        cors_read = client.get(
            "/api/v1/health", headers={"Origin": "http://localhost:4173"}
        )
        assert "X-Ego-Approval-Token" in cors_read.headers[
            "access-control-expose-headers"
        ]

        blocked = client.post("/api/v1/demo/reset", json={})
        assert blocked.status_code == 503
        assert blocked.json()["error"]["code"] == "operator_auth_not_configured"


def test_operator_key_rejects_missing_and_wrong_credentials(tmp_path: Path) -> None:
    application = create_app(
        str(tmp_path / "configured.sqlite3"),
        operator_key=TEST_OPERATOR_KEY,
        operator_id=TEST_OPERATOR_ID,
    )
    with TestClient(application) as client:
        missing = client.post("/api/v1/demo/reset", json={})
        assert missing.status_code == 401
        assert missing.headers["www-authenticate"] == "Bearer"
        assert missing.json()["error"]["code"] == "operator_auth_required"

        wrong = client.post(
            "/api/v1/demo/reset",
            json={},
            headers={"Authorization": "Bearer %s" % ("x" * 32)},
        )
        assert wrong.status_code == 403
        assert wrong.json()["error"]["code"] == "operator_auth_invalid"

        allowed = client.post(
            "/api/v1/demo/reset",
            json={},
            headers=TEST_AUTHORIZATION_HEADERS,
        )
        assert allowed.status_code == 200


def test_explicit_demo_bypass_never_authorizes_non_demo_mutations(tmp_path: Path) -> None:
    application = create_app(
        str(tmp_path / "demo-only.sqlite3"),
        operator_key="",
        operator_id=TEST_OPERATOR_ID,
        allow_unauthenticated_demo=True,
    )
    with TestClient(application) as client:
        approval = _pause_demo_for_approval(client)
        spoofed = client.post(
            "/api/v1/approvals/%s/decision" % approval["id"],
            json={
                "decision": "approved",
                "approver": "attacker.claim",
                "expected_digest": approval["action_digest"],
            },
        )
        assert spoofed.status_code == 403
        assert spoofed.json()["error"]["code"] == "approver_identity_mismatch"
        fixed_identity = client.post(
            "/api/v1/approvals/%s/decision" % approval["id"],
            json={
                "decision": "approved",
                "expected_digest": approval["action_digest"],
            },
        )
        assert fixed_identity.status_code == 200
        assert fixed_identity.json()["approval"]["approver"] == "demo.operator"

        catalog = client.get("/api/v1/skills").json()["items"]
        plan = next(item for item in catalog if item["name"] == "research-plan")
        blocked = client.post(
            "/api/v1/skills/research-plan/invoke",
            json={
                "correlation_id": "demo_bypass_must_not_cross",
                "expected_version": plan["version"],
                "expected_package_digest": plan["package_digest"],
                "payload": {},
            },
        )
        assert blocked.status_code == 503
        assert blocked.json()["error"]["code"] == "operator_auth_not_configured"


def test_approver_is_bound_to_authenticated_operator_identity(tmp_path: Path) -> None:
    application = create_app(
        str(tmp_path / "identity.sqlite3"),
        operator_key=TEST_OPERATOR_KEY,
        operator_id=TEST_OPERATOR_ID,
    )
    with TestClient(application) as client:
        client.headers.update(TEST_AUTHORIZATION_HEADERS)
        approval = _pause_demo_for_approval(client)
        spoofed = client.post(
            "/api/v1/approvals/%s/decision" % approval["id"],
            json={
                "decision": "approved",
                "approver": "caller.claimed.identity",
                "expected_digest": approval["action_digest"],
            },
        )
        assert spoofed.status_code == 403
        assert spoofed.json()["error"]["code"] == "approver_identity_mismatch"
        assert client.get("/api/v1/tasks/ego-lite-001").json()["pending_approval"][
            "status"
        ] == "pending"

        bound = client.post(
            "/api/v1/approvals/%s/decision" % approval["id"],
            json={
                "decision": "approved",
                "expected_digest": approval["action_digest"],
            },
        )
        assert bound.status_code == 200, bound.text
        assert bound.json()["approval"]["approver"] == TEST_OPERATOR_ID


def test_operator_key_comparison_uses_constant_time_digest_compare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(ValueError, match="between 32 and 4096"):
        OperatorAuthenticator(key="too-short", operator_id=TEST_OPERATOR_ID)

    authenticator = OperatorAuthenticator(
        key=TEST_OPERATOR_KEY,
        operator_id=TEST_OPERATOR_ID,
    )
    actual_compare = operator_auth_module.hmac.compare_digest
    calls: list[tuple[bytes, bytes]] = []

    def recording_compare(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return actual_compare(left, right)

    monkeypatch.setattr(operator_auth_module.hmac, "compare_digest", recording_compare)
    with pytest.raises(ControlPlaneError) as caught:
        authenticator.authenticate("Bearer %s" % ("y" * 32))
    assert caught.value.code == "operator_auth_invalid"
    assert len(calls) == 1
    assert len(calls[0][0]) == len(calls[0][1]) == 32


class _CaptureTransport:
    def __init__(self) -> None:
        self.headers: Mapping[str, str] = {}

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        json_body: Any = None,
        timeout: float = 15.0,
    ) -> HTTPResponse:
        self.headers = dict(headers or {})
        payload = {"task": {"id": "task-live", "stage": "PLAN"}}
        return HTTPResponse(
            status=200,
            headers={"content-type": "application/json"},
            body=json.dumps(payload).encode("utf-8"),
        )


def test_bridge_sends_operator_key_without_copying_it_into_receipt() -> None:
    transport = _CaptureTransport()
    client = EgoClient(
        "http://ego.invalid",
        operator_key=TEST_OPERATOR_KEY,
        transport=transport,
    )
    _, receipt = client.advance_stage_with_receipt(
        "task-live", "PLAN", "bridge-auth-regression"
    )
    assert transport.headers["Authorization"] == "Bearer %s" % TEST_OPERATOR_KEY
    assert TEST_OPERATOR_KEY not in json.dumps(receipt, sort_keys=True)
    assert TEST_OPERATOR_KEY not in repr(
        BridgeSettings(ego_operator_key=TEST_OPERATOR_KEY)
    )
