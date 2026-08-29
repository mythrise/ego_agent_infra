from __future__ import annotations

import urllib.parse
from pathlib import Path
from typing import Any, Mapping, Optional

from fastapi.testclient import TestClient

from apps.agentteams_bridge.clients import AgentTeamsClient, EgoClient, MatrixClient
from apps.agentteams_bridge.models import GrantRequest, RunState, StartRunRequest
from apps.agentteams_bridge.service import AgentTeamsBridge
from apps.agentteams_bridge.store import BridgeStore
from apps.agentteams_bridge.transport import HTTPResponse
from apps.api.main import APPROVAL_TOKEN_HEADER, create_app
from tests.api.operator_auth_helpers import (
    TEST_AUTHORIZATION_HEADERS,
    TEST_OPERATOR_ID,
    TEST_OPERATOR_KEY,
)
from tests.agentteams.conftest import (
    LIVE_CORRELATION_ID,
    LIVE_OBJECTIVE,
    LIVE_TRACE_ID,
    MutableClock,
)


class RoutedTransport:
    """Route only the Ego hostname to a real local ASGI app; all else stays fixture-only."""

    def __init__(self, ego: TestClient, agentteams: Any) -> None:
        self.ego = ego
        self.agentteams = agentteams

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Optional[Mapping[str, str]] = None,
        json_body: Any = None,
        timeout: float = 15.0,
    ) -> HTTPResponse:
        parsed = urllib.parse.urlparse(url)
        if parsed.hostname != "ego.real.invalid":
            return self.agentteams.request(
                method,
                url,
                headers=headers,
                json_body=json_body,
                timeout=timeout,
            )
        path = parsed.path + (("?" + parsed.query) if parsed.query else "")
        response = self.ego.request(
            method,
            path,
            headers=dict(headers or {}),
            json=json_body,
        )
        return HTTPResponse(
            status=response.status_code,
            headers=dict(response.headers),
            body=response.content,
        )


def _live_task_request() -> dict[str, Any]:
    config_sha = "c" * 64
    return {
        "task_id": "task-live",
        "title": "Real local control-plane bridge contract",
        "objective": LIVE_OBJECTIVE,
        "synthetic": False,
        "risk_level": "R2",
        "goal": {
            "objective": LIVE_OBJECTIVE,
            "frozen": True,
            "hardware": "one externally controlled GPU",
            "constraints": {"gpu_count": 1, "wall_time_seconds": 900, "seed": 42},
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
            "trace_id": LIVE_TRACE_ID,
            "correlation_id": LIVE_CORRELATION_ID,
            "context_version": 1,
        },
        "execution_contract": {
            "action": "gpu.launch_experiment",
            "config_sha256": config_sha,
            "action_payload": {
                "config_sha256": config_sha,
                "entrypoint": "eval_pose",
                "gpu_ids": [0],
                "seed": 42,
                "synthetic": False,
            },
            "rollback_point": "Preserve frozen inputs and terminate the bounded job",
        },
    }


def test_fake_agentteams_transport_drives_real_control_plane_finalization(
    fake_transport: Any, tmp_path: Path
) -> None:
    """Contract-only: real API/storage logic, injected AgentTeams/Matrix transport, no live claim."""

    with TestClient(
        create_app(
            str(tmp_path / "ego.sqlite3"),
            operator_key=TEST_OPERATOR_KEY,
            operator_id=TEST_OPERATOR_ID,
        )
    ) as ego_api:
        ego_api.headers.update(TEST_AUTHORIZATION_HEADERS)
        created = ego_api.post(
            "/api/v1/tasks",
            json=_live_task_request(),
            headers={"Idempotency-Key": "e2e-create-live-task"},
        )
        assert created.status_code == 201, created.text
        routed = RoutedTransport(ego_api, fake_transport)
        bridge = AgentTeamsBridge(
            BridgeStore(":memory:"),
            AgentTeamsClient(
                "http://agentteams.fixture.invalid",
                token="controller-token",
                transport=routed,
            ),
            MatrixClient(
                "http://matrix.fixture.invalid",
                access_token="matrix-token",
                transport=routed,
            ),
            EgoClient(
                "http://ego.real.invalid",
                operator_key=TEST_OPERATOR_KEY,
                transport=routed,
            ),
            clock=MutableClock(),
        )
        run = bridge.start_run(
            StartRunRequest(
                ego_task_id="task-live",
                objective=LIVE_OBJECTIVE,
                trace_id=LIVE_TRACE_ID,
                correlation_id=LIVE_CORRELATION_ID,
            )
        )
        fake_transport.complete_all_with_contracts(run)
        run = bridge.reconcile(run.id).run
        assert run.state == RunState.WAITING_R2

        task = ego_api.get("/api/v1/tasks/task-live").json()
        approval = task["pending_approval"]
        decision = ego_api.post(
            "/api/v1/approvals/%s/decision" % approval["id"],
            json={
                "decision": "approved",
                "approver": TEST_OPERATOR_ID,
                "expected_digest": approval["action_digest"],
            },
            headers={"Idempotency-Key": "e2e-approve-live-task"},
        )
        assert decision.status_code == 200, decision.text
        approval_token = decision.headers[APPROVAL_TOKEN_HEADER]
        assert decision.json()["approval_token"] is None
        run = bridge.grant_r2(
            run.id,
            GrantRequest(
                approval_token=approval_token,
                idempotency_key="e2e-consume-live-grant",
            ),
        )
        fake_transport.complete_all_with_contracts(run)
        run = bridge.reconcile(run.id).run

        assert run.state == RunState.COMPLETED
        terminal = ego_api.get("/api/v1/tasks/task-live").json()
        assert terminal["stage"] == "COMPLETED"
        assert terminal["decision"] == "KEEP"
        assert terminal["gate_result"]["status"] == "pass"
        assert len(terminal["evidence"]) == 7
        assert all(item["synthetic"] is False for item in terminal["evidence"])
        index = bridge.acceptance_input_index(run.id)
        assert index["inputs_ready_for_assembly"] is True
        assert index["bundle_assembled"] is False
        assert index["external_origin_status"] == "UNVERIFIED"
        assert index["live_claim_allowed"] is False
