from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from apps.agentteams_bridge.errors import BridgeError
from apps.agentteams_bridge.extensions.focus_memory import FocusedMemoryContext
from apps.agentteams_bridge.models import (
    OFFICIAL_MAIN_COMMIT,
    CollaborationEnvelope,
    EnvelopeKind,
    StartRunRequest,
    canonical_sha256,
)
from apps.agentteams_bridge.store import BridgeStore as SQLiteBridgeStore
from apps.api.trusted_memory.focus_contracts import (
    FocusMemoryQuery,
    TrustedFocusFact,
    TrustedMemoryFocusSource,
    build_focus_evidence_commitment,
    build_trusted_memory_focus_source,
)
from apps.api.trusted_memory.models import DecisionOutcome, MemoryOrigin


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
PROJECT_ID = "ego-task-live-v1-a17ccd18"

WORKER_SKILLS = {
    "ego-scout": ["research-memory"],
    "ego-architect": ["research-plan", "ablation-analyzer"],
    "ego-runtime": ["safe-experiment-runner", "dataset-manifest"],
    "ego-evaluator": ["ablation-analyzer"],
    "ego-reviewer": ["evidence-gate"],
    "ego-memory-curator": ["research-memory"],
}


def _focused_service_module():
    try:
        from apps.agentteams_bridge import focused_service
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(f"Focused AgentTeams bridge module is missing: {exc}")
    return focused_service


def _source() -> TrustedMemoryFocusSource:
    query = FocusMemoryQuery(
        tenant_id="tenant-1",
        project_id=PROJECT_ID,
        outcomes=(DecisionOutcome.KEEP,),
        origins=(MemoryOrigin.LOCAL_TRUSTED,),
        max_items=16,
        scan_limit=64,
    )
    fact = TrustedFocusFact(
        fact_sha256=SHA_A,
        tenant_id="tenant-1",
        project_id=PROJECT_ID,
        lineage_id="lineage-1",
        revision_id="revision-1",
        revision=1,
        fact_kind="constraint",
        statement="Never bypass the Evidence Gate during AgentTeams execution.",
        component="evidence",
        version="v1",
        outcome=DecisionOutcome.KEEP,
        origin=MemoryOrigin.LOCAL_TRUSTED,
        evidence_commitment=build_focus_evidence_commitment(
            evidence_ids=("evidence-1",),
            evidence_digests=(SHA_D,),
            decision_closure_digest=SHA_C,
        ),
        provenance_sha256=SHA_B,
        projection_event_hash=SHA_A,
    )
    return build_trusted_memory_focus_source(
        query,
        (fact,),
        scanned_count=1,
        truncated_by_scan_limit=False,
    )


@dataclass
class _Provider:
    source: TrustedMemoryFocusSource
    calls: int = 0
    error: Optional[Exception] = None

    def fetch(
        self,
        *,
        tenant_id: str,
        project_id: str,
        max_items: int,
        scan_limit: int,
    ):
        self.calls += 1
        if self.error is not None:
            raise self.error
        module = _focused_service_module()
        return module.FocusMemoryFetch(
            source=self.source,
            receipt={
                "schema": "egoagentos.agentteams-upstream-receipt/v1",
                "operation": "fetch-trusted-memory-focus",
                "request": {"tenant_id": tenant_id, "project_id": project_id},
                "response": {"source_sha256": self.source.source_sha256},
                "status": 200,
            },
        )


class _Transport:
    def __init__(self) -> None:
        self.projects: dict[str, dict[str, Any]] = {}
        self.sent_messages: list[dict[str, Any]] = []

    def __call__(
        self,
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> tuple[int, dict[str, str], bytes]:
        del headers, timeout
        path = url.split("?", 1)[0]
        payload = {} if body is None else __import__("json").loads(body.decode("utf-8"))
        if method == "GET" and path.endswith("/healthz"):
            return 200, {}, b"ok"
        if method == "GET" and path.endswith("/api/v1/version"):
            return 200, {}, b'{"controller":"test","gitCommit":"%s"}' % OFFICIAL_MAIN_COMMIT.encode()
        if method == "GET" and path.endswith("/_matrix/client/v3/account/whoami"):
            return 200, {}, b'{"user_id":"@bridge:test","device_id":"DEV"}'
        if method == "GET" and path.endswith("/api/v1/projects"):
            projects = list(self.projects.values())
            return 200, {}, __import__("json").dumps({"projects": projects, "total": len(projects)}).encode()
        if method == "GET" and "/api/v1/teams/" in path:
            return 200, {}, __import__("json").dumps(
                {
                    "name": "ego-researchops",
                    "teamName": "ego-researchops",
                    "phase": "Active",
                    "workerMembers": [
                        {"name": name, "role": "worker"} for name in WORKER_SKILLS
                    ],
                    "leaderName": "ego-scout",
                    "teamRoomID": "!team:test",
                    "leaderReady": True,
                    "readyWorkers": len(WORKER_SKILLS),
                    "totalWorkers": len(WORKER_SKILLS),
                }
            ).encode()
        if method == "POST" and path.endswith("/ensure-ready"):
            name = path.split("/api/v1/workers/", 1)[1].split("/", 1)[0]
            return 200, {}, __import__("json").dumps(
                {"name": name, "phase": "Ready", "skills": WORKER_SKILLS[name]}
            ).encode()
        if method == "GET" and "/api/v1/workers/" in path:
            name = path.rsplit("/", 1)[-1]
            return 200, {}, __import__("json").dumps(
                {
                    "name": name,
                    "phase": "Running",
                    "state": "Running",
                    "skills": WORKER_SKILLS[name],
                    "matrixUserID": "@%s:test" % name,
                    "roomID": "!%s:test" % name,
                    "team": "ego-researchops",
                    "role": "worker",
                }
            ).encode()
        if method == "POST" and path.endswith("/api/v1/projects"):
            project_id = payload["project_id"]
            project = {
                "project_id": project_id,
                "title": payload["title"],
                "team_id": payload["team_id"],
                "status": "active",
                "plan_type": "dag",
                "mode": "project",
                "nodes": [],
                "edges": [],
                "next": [],
                "interrupts": [],
                "tasks_detail": [],
            }
            self.projects[project_id] = project
            return 201, {}, __import__("json").dumps(project).encode()
        if method == "POST" and path.endswith("/replan"):
            project_id = path.split("/api/v1/projects/", 1)[1].split("/", 1)[0]
            project = self.projects[project_id]
            project["nodes"] = [
                {
                    "id": task["taskId"],
                    "name": task["title"],
                    "assignee": task["assignedTo"],
                    "status": "pending",
                }
                for task in payload["tasks"]
            ]
            return 200, {}, __import__("json").dumps(project).encode()
        if method == "GET" and path.endswith("/workflow"):
            project_id = path.split("/api/v1/projects/", 1)[1].split("/", 1)[0]
            return 200, {}, __import__("json").dumps(self.projects[project_id]).encode()
        if method == "PUT" and "/_matrix/client/v3/rooms/" in path:
            self.sent_messages.append(payload)
            return 200, {}, b'{"event_id":"$matrix-focus"}'
        if method == "GET" and path.endswith("/api/v1/tasks/task-live"):
            return 200, {}, __import__("json").dumps(
                {
                    "id": "task-live",
                    "scenario": "external_live",
                    "synthetic_demo": False,
                    "objective": "Use trusted memory in AgentTeams",
                    "stage": "INTAKE",
                    "live_source": {
                        "source": "agentteams",
                        "team": "ego-researchops",
                        "trace_id": "trace_focus_live_001",
                        "correlation_id": "corr_focus_live_001",
                        "context_version": 1,
                        "origin_authentication": "UNVERIFIED_OPERATOR_ASSERTION",
                    },
                }
            ).encode()
        raise AssertionError((method, url, payload))

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: Any = None,
        timeout: float = 15.0,
    ):
        from apps.agentteams_bridge.transport import HTTPResponse

        body = None
        if json_body is not None:
            body = __import__("json").dumps(json_body).encode("utf-8")
        status, response_headers, response_body = self(
            method,
            url,
            headers or {},
            body,
            timeout,
        )
        return HTTPResponse(status, response_headers, response_body)


def _service(tmp_path, provider: _Provider, *, mode: str = "required"):
    from apps.agentteams_bridge.clients import AgentTeamsClient, EgoClient, MatrixClient

    module = _focused_service_module()
    transport = _Transport()
    service = module.FocusedAgentTeamsBridge(
        SQLiteBridgeStore(str(tmp_path / "bridge.sqlite3")),
        AgentTeamsClient("http://controller.test", transport=transport),
        MatrixClient("http://matrix.test", access_token="matrix-token", transport=transport),
        EgoClient("http://ego.test", transport=transport),
        focus_memory_provider=provider,
        focus_memory_mode=module.FocusMemoryMode(mode),
        focus_memory_tenant_id="tenant-1",
        focus_memory_token_budget=20_000,
        focus_memory_max_items=8,
        focus_memory_source_max_items=16,
        focus_memory_scan_limit=64,
    )
    return service, transport


def _request() -> StartRunRequest:
    return StartRunRequest(
        ego_task_id="task-live",
        team="ego-researchops",
        trace_id="trace_focus_live_001",
        correlation_id="corr_focus_live_001",
        context_version=1,
        objective="Use trusted memory in AgentTeams",
        mode="live",
    )


def test_required_focus_memory_is_bound_into_real_task_request_envelope(tmp_path) -> None:
    provider = _Provider(_source())
    service, transport = _service(tmp_path, provider)

    run = service.start_run(_request())

    assert run.state.value == "PRE_APPROVAL"
    assert provider.calls == 1
    sent = transport.sent_messages[-1]
    envelope = CollaborationEnvelope.model_validate(sent["com.egoagentos.envelope"])
    assert envelope.kind is EnvelopeKind.TASK_REQUEST
    focus = envelope.body["focus_memory"]
    assert focus["status"] == "READY"
    assert focus["source_sha256"] == provider.source.source_sha256
    assert focus["memory_snapshot_root"] == provider.source.memory_snapshot_root
    assert tuple(focus["contexts"]) == tuple(sorted(task.task_id for task in run.task_graph))
    for task_id, context_payload in focus["contexts"].items():
        context = FocusedMemoryContext.model_validate(context_payload)
        assert context.task_id == task_id
        assert context.items[0].statement.startswith("Never bypass")
        assert context.items[0].evidence_commitment.association == (
            "UNPAIRED_SETS_BOUND_BY_DECISION_CLOSURE"
        )
        assert context.source_sha256 == provider.source.source_sha256
    assert envelope.body_sha256 == canonical_sha256(envelope.body)
    receipts = service.store.receipts(run.id)
    focus_receipts = [
        item for item in receipts["items"] if item["kind"] == "trusted-memory-focus-source"
    ]
    assert len(focus_receipts) == 1


def test_required_focus_memory_failure_enters_existing_compensation_path(tmp_path) -> None:
    provider = _Provider(
        _source(),
        error=BridgeError(
            "egoagentos_trusted_memory_unavailable",
            "memory source unavailable",
            status_code=503,
            retryable=True,
        ),
    )
    service, transport = _service(tmp_path, provider)

    with pytest.raises(BridgeError, match="focus memory|focus_memory|dispatch") as caught:
        service.start_run(_request())

    assert caught.value.details["compensation_operation"] == "start-dispatch"
    projects = service.store.active_runs()
    assert len(projects) == 1
    run = projects[0]
    assert run.state.value == "COMPENSATION_REQUIRED"
    assert run.checkpoint["compensation_retry"]["operation"] == "start-dispatch"
    assert transport.sent_messages == []


def test_disabled_focus_memory_is_explicit_and_never_reported_as_ready(tmp_path) -> None:
    provider = _Provider(_source())
    service, transport = _service(tmp_path, provider, mode="disabled")

    run = service.start_run(_request())

    assert run.state.value == "PRE_APPROVAL"
    assert provider.calls == 0
    envelope = CollaborationEnvelope.model_validate(
        transport.sent_messages[-1]["com.egoagentos.envelope"]
    )
    focus = envelope.body["focus_memory"]
    assert focus["status"] == "DISABLED"
    assert focus["mode"] == "disabled"
    assert "source_sha256" not in focus
