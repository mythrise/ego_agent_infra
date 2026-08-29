from __future__ import annotations

import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest

from apps.agentteams_bridge.errors import BridgeError, UpstreamError
from apps.agentteams_bridge.models import (
    EnvelopeKind,
    GrantRequest,
    RunState,
    StartRunRequest,
)
from apps.agentteams_bridge.service import AgentTeamsBridge
from apps.agentteams_bridge.store import BridgeStore
from apps.agentteams_bridge.transport import TransportFailure
from apps.api.models import FinalizeTaskRequest
from benchmarks.trace_verifier import _verify_bridge_event_chain
from integrations.agentteams.benchmark_adapter import (
    REQUIRED_TRACE_EVENTS,
    TRACE_SCHEMA_VERSION,
    _bind_scenario_proof,
    _build_verified_trace,
    _write_trace,
)
from tests.agentteams.conftest import (
    LIVE_CORRELATION_ID,
    LIVE_OBJECTIVE,
    LIVE_TRACE_ID,
)


def _start(bridge):
    return bridge.start_run(
        StartRunRequest(
            ego_task_id="task-live",
            objective=LIVE_OBJECTIVE,
            trace_id=LIVE_TRACE_ID,
            correlation_id=LIVE_CORRELATION_ID,
            ack_timeout_seconds=5,
            execution_timeout_seconds=30,
        )
    )


def _shared_store_bridge(bridge, path, clock) -> AgentTeamsBridge:
    return AgentTeamsBridge(
        BridgeStore(str(path)),
        bridge.agentteams,
        bridge.matrix,
        bridge.ego,
        clock=clock,
    )


def test_live_start_reserves_before_create_and_recovers_uncertain_create(
    bridge, fake_transport, monkeypatch
) -> None:
    original_create = bridge.agentteams.create_project_with_receipt

    def create_then_crash(**kwargs):
        original_create(**kwargs)
        reservations = bridge.store.active_runs()
        assert len(reservations) == 1
        assert reservations[0].agentteams_project_id == kwargs["project_id"]
        raise RuntimeError("simulated crash after remote create")

    monkeypatch.setattr(bridge.agentteams, "create_project_with_receipt", create_then_crash)
    with pytest.raises(RuntimeError, match="simulated crash"):
        _start(bridge)

    reservation = bridge.store.active_runs()[0]
    assert reservation.state == RunState.PROVISIONING

    def already_created(**_kwargs):
        raise UpstreamError(
            "agentteams",
            "create-project",
            409,
            "project already exists",
        )

    monkeypatch.setattr(bridge.agentteams, "create_project_with_receipt", already_created)
    recovered = _start(bridge)
    assert recovered.id == reservation.id
    assert recovered.state == RunState.PRE_APPROVAL
    assert recovered.checkpoint["project_create_confirmation"] == (
        "RECOVERED_FROM_OFFICIAL_WORKFLOW"
    )


def test_live_start_recovers_persisted_receipt_after_confirmation_write_crash(
    bridge, fake_transport, monkeypatch
) -> None:
    original_update = bridge.store.update_run
    interrupted = False

    def crash_before_confirmation(run, *, expected_version, lease_owner=None):
        nonlocal interrupted
        if run.checkpoint.get("project_create_committed") and not interrupted:
            interrupted = True
            raise RuntimeError("simulated crash before project confirmation write")
        return original_update(
            run,
            expected_version=expected_version,
            lease_owner=lease_owner,
        )

    monkeypatch.setattr(bridge.store, "update_run", crash_before_confirmation)
    with pytest.raises(RuntimeError, match="before project confirmation write"):
        _start(bridge)
    create_calls = [
        call
        for call in fake_transport.calls
        if call["method"] == "POST" and call["path"] == "/api/v1/projects"
    ]
    assert len(create_calls) == 1

    monkeypatch.setattr(bridge.store, "update_run", original_update)
    recovered = _start(bridge)
    assert recovered.checkpoint["project_create_confirmation"] == (
        "RECOVERED_FROM_PERSISTED_RECEIPT"
    )
    receipt = bridge.store.receipts(recovered.id)["items"][0]
    assert recovered.checkpoint["project_create_response_sha256"] == (
        receipt["payload"]["response_sha256"]
    )
    create_calls = [
        call
        for call in fake_transport.calls
        if call["method"] == "POST" and call["path"] == "/api/v1/projects"
    ]
    assert len(create_calls) == 1


def test_live_start_lease_blocks_second_process_before_project_create(
    bridge, fake_transport, clock, tmp_path, monkeypatch
) -> None:
    database = tmp_path / "bridge-start.sqlite3"
    first = _shared_store_bridge(bridge, database, clock)
    second = _shared_store_bridge(bridge, database, clock)
    original_create = bridge.agentteams.create_project_with_receipt
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def blocked_create(**kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            entered.set()
            assert release.wait(timeout=5)
        return original_create(**kwargs)

    monkeypatch.setattr(bridge.agentteams, "create_project_with_receipt", blocked_create)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(_start, first)
        assert entered.wait(timeout=5)
        try:
            with pytest.raises(BridgeError) as busy:
                _start(second)
            assert busy.value.code == "operation_in_progress"
            assert busy.value.retryable is True
            assert calls == 1
        finally:
            release.set()
        assert pending.result(timeout=5).state == RunState.PRE_APPROVAL


def test_reconcile_lease_blocks_cross_connection_duplicate_observation(
    bridge, fake_transport, clock, tmp_path, monkeypatch
) -> None:
    database = tmp_path / "bridge-reconcile.sqlite3"
    first = _shared_store_bridge(bridge, database, clock)
    run = _start(first)
    second = _shared_store_bridge(bridge, database, clock)
    original_workflow = bridge.agentteams.workflow_with_receipt
    entered = threading.Event()
    release = threading.Event()
    calls = 0
    calls_lock = threading.Lock()

    def blocked_workflow(*args, **kwargs):
        nonlocal calls
        with calls_lock:
            calls += 1
            call_number = calls
        if call_number == 1:
            entered.set()
            assert release.wait(timeout=5)
        return original_workflow(*args, **kwargs)

    monkeypatch.setattr(bridge.agentteams, "workflow_with_receipt", blocked_workflow)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(first.reconcile, run.id)
        assert entered.wait(timeout=5)
        try:
            with pytest.raises(BridgeError) as busy:
                second.reconcile(run.id)
            assert busy.value.code == "operation_in_progress"
            assert busy.value.retryable is True
            assert calls == 1
        finally:
            release.set()
        assert pending.result(timeout=5).run.state == RunState.PRE_APPROVAL


def test_sqlite_operation_lease_timeout_and_owner_fencing(
    bridge, fake_transport, clock, tmp_path, monkeypatch
) -> None:
    database = tmp_path / "bridge-lease-fencing.sqlite3"
    first = _shared_store_bridge(bridge, database, clock)
    run = _start(first)
    second = _shared_store_bridge(bridge, database, clock)
    monkeypatch.setattr("apps.agentteams_bridge.store.utc_now", clock)

    claimed = first.store.claim_operation(
        run.id,
        "lease-test",
        "owner-a",
        timeout_seconds=30,
    )
    lease = claimed.checkpoint["_operation_lease"]
    assert lease["timeout_seconds"] == 30
    assert lease["expires_at"] == (clock.value + timedelta(seconds=30)).isoformat()

    with pytest.raises(BridgeError) as busy:
        second.store.claim_operation(
            run.id,
            "lease-test",
            "owner-b",
            timeout_seconds=30,
        )
    assert busy.value.code == "operation_in_progress"
    assert busy.value.details["timeout_seconds"] == 30

    with pytest.raises(BridgeError) as wrong_owner:
        second.store.update_run(
            second.store.get_run(run.id),
            expected_version=run.version,
            lease_owner="owner-b",
        )
    assert wrong_owner.value.code == "operation_lease_lost"

    checkpoint_without_lease = dict(claimed.checkpoint)
    checkpoint_without_lease.pop("_operation_lease")
    with pytest.raises(BridgeError) as lease_mutation:
        first.store.update_run(
            claimed.model_copy(update={"checkpoint": checkpoint_without_lease}),
            expected_version=run.version,
            lease_owner="owner-a",
        )
    assert lease_mutation.value.code == "operation_lease_mutation"

    clock.value += timedelta(seconds=31)
    with pytest.raises(BridgeError) as expired:
        first.store.update_run(
            claimed,
            expected_version=run.version,
            lease_owner="owner-a",
        )
    assert expired.value.code == "operation_lease_lost"
    assert expired.value.details["reason"] == "expired"

    reclaimed = second.store.claim_operation(
        run.id,
        "lease-test-recovery",
        "owner-b",
        timeout_seconds=30,
    )
    first.store.release_operation(run.id, "owner-a")
    assert second.store.get_run(run.id).checkpoint["_operation_lease"] == (
        reclaimed.checkpoint["_operation_lease"]
    )

    stale_envelope = first._envelope(
        claimed,
        EnvelopeKind.TASK_UPDATE,
        {"stale_owner": "owner-a", "takeover_owner": "owner-b"},
    )
    event_total = first.store.events(run.id)["total"]
    receipt_total = first.store.receipts(run.id)["total"]
    with pytest.raises(BridgeError) as stale_event:
        first.store.append_event(
            run.id,
            stale_envelope,
            lease_owner="owner-a",
        )
    assert stale_event.value.code == "operation_lease_lost"
    with pytest.raises(BridgeError) as stale_receipt:
        first.store.archive_receipt(
            run.id,
            receipt_key="stale-owner:receipt",
            source="test",
            kind="takeover-regression",
            payload={"owner": "owner-a"},
            lease_owner="owner-a",
        )
    assert stale_receipt.value.code == "operation_lease_lost"
    assert first.store.events(run.id)["total"] == event_total
    assert first.store.receipts(run.id)["total"] == receipt_total

    matrix_calls = len(
        [
            call
            for call in fake_transport.calls
            if "/send/m.room.message/" in call["path"]
        ]
    )
    with pytest.raises(BridgeError) as stale_matrix:
        first._send(claimed, stale_envelope, "owner-a")
    assert stale_matrix.value.code == "operation_lease_lost"
    assert (
        len(
            [
                call
                for call in fake_transport.calls
                if "/send/m.room.message/" in call["path"]
            ]
        )
        == matrix_calls
    )

    stale_waiting = claimed.model_copy(
        update={
            "state": RunState.WAITING_R2,
            "checkpoint": {
                **claimed.checkpoint,
                "ego_grant_committed": True,
            },
        }
    )
    controller_mutations = len(
        [
            call
            for call in fake_transport.calls
            if call["method"] == "POST"
            and (call["path"].endswith("/resume") or call["path"].endswith("/replan"))
        ]
    )
    with pytest.raises(BridgeError) as stale_controller:
        first._grant_r2_claimed(
            stale_waiting,
            GrantRequest(
                approval_token="stale-owner-approval-token",
                idempotency_key="stale-owner-grant",
            ),
            "owner-a",
        )
    assert stale_controller.value.code == "operation_lease_lost"
    assert (
        len(
            [
                call
                for call in fake_transport.calls
                if call["method"] == "POST"
                and (
                    call["path"].endswith("/resume")
                    or call["path"].endswith("/replan")
                )
            ]
        )
        == controller_mutations
    )
    second.store.release_operation(run.id, "owner-b")


def test_live_start_uses_controller_team_workers_project_and_matrix(bridge, fake_transport) -> None:
    run = _start(bridge)
    assert run.state == RunState.PRE_APPROVAL
    assert run.mode == "live"
    assert len(run.task_graph) == 3
    assert {task.assigned_worker for task in run.task_graph} == {
        "ego-scout",
        "ego-architect",
        "ego-reviewer",
    }
    calls = [(call["method"], call["path"]) for call in fake_transport.calls]
    assert ("GET", "/healthz") in calls
    assert ("GET", "/api/v1/projects") in calls
    assert ("GET", "/_matrix/client/v3/account/whoami") in calls
    assert ("POST", "/api/v1/projects") in calls
    assert any(path.endswith("/replan") and method == "POST" for method, path in calls)
    matrix_call = next(call for call in fake_transport.calls if "/send/m.room.message/" in call["path"])
    assert matrix_call["json"]["com.egoagentos.envelope"]["schema"] == (
        "egoagentos.agentteams-envelope.v2"
    )
    assert matrix_call["json"]["m.mentions"]["user_ids"] == [
        "@ego-research-lead:matrix.fixture.invalid"
    ]
    events = bridge.store.events(run.id)
    assert events["chain_valid"] is True
    assert events["items"][0]["kind"] == "TASK_REQUEST"
    receipts = bridge.store.receipts(run.id)
    assert receipts["chain_valid"] is True
    assert {item["receipt_key"] for item in receipts["items"]} >= {
        "agentteams:project-create",
    }
    matrix_receipt = next(item for item in receipts["items"] if item["source"] == "matrix")
    assert matrix_receipt["payload"]["request_body"]["com.egoagentos.envelope"]["kind"] == (
        "TASK_REQUEST"
    )
    assert "matrix-token" not in json.dumps(receipts)


def test_live_start_rejects_unbound_or_implicitly_synthetic_ego_tasks(
    bridge, fake_transport
) -> None:
    fake_transport.ego_task.pop("live_source")
    with pytest.raises(BridgeError) as missing_binding:
        _start(bridge)
    assert missing_binding.value.code == "ego_live_binding_conflict"

    fake_transport.ego_task["synthetic_demo"] = None
    with pytest.raises(BridgeError) as implicit_truth:
        _start(bridge)
    assert implicit_truth.value.code == "synthetic_task_rejected"


def test_receipt_archive_is_idempotent_but_rejects_key_content_conflicts(
    bridge,
) -> None:
    run = _start(bridge)
    payload = {"event_id": "$same", "raw": {"body": "message"}}
    first = bridge.store.archive_receipt(
        run.id,
        receipt_key="test:receipt",
        source="matrix",
        kind="raw-message",
        payload=payload,
    )
    replay = bridge.store.archive_receipt(
        run.id,
        receipt_key="test:receipt",
        source="matrix",
        kind="raw-message",
        payload=payload,
    )
    assert first["receipt_hash"] == replay["receipt_hash"]
    assert replay["idempotent_replay"] is True
    with pytest.raises(BridgeError) as conflict:
        bridge.store.archive_receipt(
            run.id,
            receipt_key="test:receipt",
            source="matrix",
            kind="raw-message",
            payload={"event_id": "$different"},
        )
    assert conflict.value.code == "receipt_key_conflict"


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (403, "agentteams_forbidden", False),
        (404, "agentteams_not_found", False),
        (409, "agentteams_conflict", True),
    ],
)
def test_official_controller_failures_are_structured(
    bridge, fake_transport, status, code, retryable
) -> None:
    fake_transport.fail_next = (
        "GET",
        "/api/v1/teams/ego-researchops",
        status,
        {"error": "contract fault"},
    )
    with pytest.raises(UpstreamError) as raised:
        bridge.agentteams.get_team("ego-researchops")
    assert raised.value.code == code
    assert raised.value.retryable is retryable
    assert raised.value.details["http_status"] == status


def test_controller_transport_failure_is_structured_and_retryable(
    bridge, fake_transport, monkeypatch
) -> None:
    monkeypatch.setattr(
        fake_transport,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TransportFailure("offline")),
    )
    with pytest.raises(UpstreamError) as raised:
        bridge.agentteams.health()
    assert raised.value.code == "agentteams_unavailable"
    assert raised.value.retryable is True
    assert raised.value.details["http_status"] == 503


def test_completed_preapproval_tasks_pause_at_real_r2_gate(bridge, fake_transport) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    result = bridge.reconcile(run.id)
    assert result.live is True
    assert result.run.state == RunState.WAITING_R2
    assert result.run.checkpoint["accepted_contracts"].keys() == {
        task.task_id for task in run.task_graph
    }
    assert any(action["action"] == "r2_paused" for action in result.actions)
    assert any(call["path"].endswith("/pause") for call in fake_transport.calls)
    assert bridge.store.events(run.id)["chain_valid"] is True
    assert fake_transport.ego_task["stage"] == "APPROVAL"
    assert fake_transport.ego_task["pending_approval"]["id"] == "apr-live-fixture"
    ego_stage_targets = [
        call["json"]["target"]
        for call in fake_transport.calls
        if call["path"] == "/api/v1/tasks/task-live/advance"
    ]
    assert ego_stage_targets == ["CONTEXT", "PLAN", "PLAN_REVIEW", "APPROVAL"]


def test_r2_grant_consumes_ego_token_then_resumes_replans_and_never_persists_token(
    bridge, fake_transport
) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "APPROVAL",
        "pending_approval": {"id": "apr-live", "status": "approved"},
    }
    token = "one-time-r2-token-never-persisted"
    updated = bridge.grant_r2(
        run.id,
        GrantRequest(approval_token=token, idempotency_key="grant-live-0001"),
    )
    assert updated.state == RunState.POST_APPROVAL
    assert updated.checkpoint["ego_grant_committed"] is True
    assert len({task.assigned_worker for task in updated.task_graph}) >= 5
    assert any(call["path"].endswith("/resume") for call in fake_transport.calls)
    assert any(call["path"].endswith("/replan") for call in fake_transport.calls)
    persisted = json.dumps(updated.model_dump(mode="json"))
    persisted += json.dumps(bridge.store.events(run.id))
    assert token not in persisted


def test_post_grant_failure_is_fenced_and_retry_does_not_consume_token_again(
    bridge, fake_transport
) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "APPROVAL",
        "pending_approval": {"id": "apr-live", "status": "approved"},
    }
    replan_path = "/api/v1/projects/%s/replan" % run.agentteams_project_id
    fake_transport.fail_next = ("POST", replan_path, 409, {"error": "concurrent write"})
    with pytest.raises(UpstreamError) as raised:
        bridge.grant_r2(
            run.id,
            GrantRequest(approval_token="token-for-compensation", idempotency_key="grant-0002"),
        )
    assert raised.value.code == "agentteams_conflict"
    fenced = bridge.get_run(run.id)
    assert fenced.state == RunState.COMPENSATION_REQUIRED
    assert fenced.checkpoint["ego_grant_committed"] is True
    assert fenced.checkpoint["compensation_retry"]["token_required"] is False
    assert any(call["path"].endswith("/pause") for call in fake_transport.calls[-3:])

    advance_count = sum(call["path"].endswith("/advance") for call in fake_transport.calls)
    recovered = bridge.grant_r2(
        run.id,
        GrantRequest(approval_token="ignored-on-retry", idempotency_key="grant-0002"),
    )
    assert recovered.state == RunState.POST_APPROVAL
    assert sum(call["path"].endswith("/advance") for call in fake_transport.calls) == advance_count


def test_successful_grant_response_without_execute_state_is_fenced(
    bridge, fake_transport, monkeypatch
) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "APPROVAL",
        "pending_approval": {"id": "apr-uncertain", "status": "approved"},
    }
    monkeypatch.setattr(
        bridge.ego,
        "consume_r2_grant",
        lambda *_args, **_kwargs: {"task": {"stage": "APPROVAL"}},
    )
    token = "uncertain-grant-token"
    with pytest.raises(BridgeError) as raised:
        bridge.grant_r2(
            run.id,
            GrantRequest(approval_token=token, idempotency_key="grant-uncertain"),
        )
    assert raised.value.code == "ego_grant_transition_unverified"
    fenced = bridge.get_run(run.id)
    assert fenced.state == RunState.COMPENSATION_REQUIRED
    assert fenced.checkpoint["ego_grant_committed"] is True
    assert fenced.checkpoint["compensation_retry"]["operation"] == (
        "grant-response-uncertain"
    )
    assert token not in json.dumps(fenced.model_dump(mode="json"))


def test_ack_timeout_cancels_and_reassigns_through_official_endpoints(
    bridge, fake_transport, clock
) -> None:
    run = _start(bridge)
    first = run.task_graph[0]
    fake_transport.workflow["nodes"][0]["status"] = "delegated"
    fake_transport.workflow["tasks_detail"] = [
        {
            "task_id": first.task_id,
            "project_id": run.agentteams_project_id,
            "status": "assigned",
            "assigned_to": first.assigned_to,
            "deliverables": [],
        }
    ]
    observed = bridge.reconcile(run.id).run
    assert observed.state == RunState.PRE_APPROVAL
    clock.value += timedelta(seconds=6)
    result = bridge.reconcile(run.id)
    assert any(action["action"] == "reassigned" for action in result.actions)
    replacement = next(task for task in result.run.task_graph if task.origin_task_id == first.task_id)
    assert replacement.task_id.endswith("-r1")
    assert replacement.assigned_worker == "ego-memory-curator"
    paths = [call["path"] for call in fake_transport.calls]
    assert any(path.endswith("/cancel") for path in paths)
    assert any(path.endswith("/replan") for path in paths)


def test_reassigned_attempt_can_reach_r2_without_reopening_superseded_task(
    bridge, fake_transport, clock
) -> None:
    run = _start(bridge)
    original = run.task_graph[0]
    fake_transport.workflow["nodes"][0]["status"] = "delegated"
    bridge.reconcile(run.id)
    clock.value += timedelta(seconds=6)
    reassigned = bridge.reconcile(run.id).run
    replacement = next(
        task for task in reassigned.task_graph if task.origin_task_id == original.task_id
    )

    fake_transport.complete_all_with_contracts(reassigned)
    for node in fake_transport.workflow["nodes"]:
        if node["id"] == original.task_id:
            node["status"] = "blocked"
    fake_transport.workflow["tasks_detail"] = [
        detail
        for detail in fake_transport.workflow["tasks_detail"]
        if detail["task_id"] != original.task_id
    ]
    waiting = bridge.reconcile(run.id).run
    assert waiting.state == RunState.WAITING_R2
    assert original.task_id not in waiting.checkpoint["accepted_contracts"]
    assert replacement.task_id in waiting.checkpoint["accepted_contracts"]

    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "APPROVAL",
        "pending_approval": {"id": "apr-reassigned", "status": "approved"},
    }
    resumed = bridge.grant_r2(
        run.id,
        GrantRequest(
            approval_token="token-after-real-reassignment",
            idempotency_key="grant-reassigned-1",
        ),
    )
    assert len({task.task_id for task in resumed.task_graph}) == len(resumed.task_graph)
    old = next(task for task in resumed.task_graph if task.task_id == original.task_id)
    assert old.status == "blocked"
    execute = next(task for task in resumed.task_graph if task.stage == "EXECUTE")
    plan_review = next(task for task in resumed.task_graph if task.stage == "PLAN_REVIEW")
    assert execute.depends_on == [plan_review.task_id]


def test_matrix_failure_at_r2_pause_enters_and_recovers_compensation(
    bridge, fake_transport, monkeypatch
) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    original_send = bridge.matrix.send_envelope_with_receipt

    def fail_send(**_kwargs):
        raise UpstreamError("matrix", "send-envelope", 503, "offline")

    monkeypatch.setattr(bridge.matrix, "send_envelope_with_receipt", fail_send)
    fenced = bridge.reconcile(run.id)
    assert fenced.run.state == RunState.COMPENSATION_REQUIRED
    assert fenced.run.checkpoint["compensation_retry"]["operation"] == (
        "approval-required-notify"
    )
    assert any(action["action"] == "compensation_required" for action in fenced.actions)

    monkeypatch.setattr(bridge.matrix, "send_envelope_with_receipt", original_send)
    recovered = bridge.reconcile(run.id)
    assert recovered.run.state == RunState.WAITING_R2
    assert recovered.actions[0]["action"] == "compensation_recovered"
    assert "compensation_retry" not in recovered.run.checkpoint


def test_terminal_matrix_failure_recovers_without_replaying_ego_finalization(
    bridge, fake_transport, monkeypatch
) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "pending_approval": {
            **fake_transport.ego_task["pending_approval"],
            "status": "approved",
            "approver": "human-operator",
        },
    }
    run = bridge.grant_r2(
        run.id,
        GrantRequest(
            approval_token="terminal-compensation-token",
            idempotency_key="terminal-grant-0001",
        ),
    )
    fake_transport.complete_all_with_contracts(run)
    original_send = bridge.matrix.send_envelope_with_receipt

    def fail_terminal(**kwargs):
        if kwargs["envelope"]["kind"] == "TERMINAL":
            raise UpstreamError("matrix", "send-envelope", 503, "one-shot outage")
        return original_send(**kwargs)

    monkeypatch.setattr(bridge.matrix, "send_envelope_with_receipt", fail_terminal)
    fenced = bridge.reconcile(run.id)
    assert fenced.run.state == RunState.COMPENSATION_REQUIRED
    assert fenced.run.checkpoint["ego_finalization_committed"] is True
    assert fenced.run.checkpoint["compensation_retry"]["operation"] == "terminal-notify"
    assert len(fake_transport.finalization_requests) == 1

    monkeypatch.setattr(bridge.matrix, "send_envelope_with_receipt", original_send)
    recovered = bridge.reconcile(run.id)
    assert recovered.run.state == RunState.COMPLETED
    assert recovered.actions[0]["action"] == "compensation_recovered"
    assert len(fake_transport.finalization_requests) == 1


def test_bridge_refuses_terminal_claim_when_ego_gate_is_not_verified(
    bridge, fake_transport, monkeypatch
) -> None:
    run = _start(bridge)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "pending_approval": {
            **fake_transport.ego_task["pending_approval"],
            "status": "approved",
            "approver": "human-operator",
        },
    }
    run = bridge.grant_r2(
        run.id,
        GrantRequest(
            approval_token="gate-failure-token",
            idempotency_key="gate-failure-grant",
        ),
    )
    fake_transport.complete_all_with_contracts(run)

    def invalid_finalization(*_args, **_kwargs):
        return (
            {
                "task": {
                    **fake_transport.ego_task,
                    "stage": "COMPLETED",
                    "decision": "KEEP",
                    "gate_result": {"status": "fail"},
                }
            },
            {"response_sha256": "f" * 64},
        )

    monkeypatch.setattr(bridge.ego, "finalize_live", invalid_finalization)
    complete_calls_before = sum(
        call["path"].endswith("/complete") for call in fake_transport.calls
    )
    with pytest.raises(BridgeError) as rejected:
        bridge.reconcile(run.id)
    assert rejected.value.code == "ego_finalization_unverified"
    assert bridge.get_run(run.id).state == RunState.POST_APPROVAL
    assert sum(call["path"].endswith("/complete") for call in fake_transport.calls) == (
        complete_calls_before
    )
    assert not any(
        item["receipt_key"] == "ego:live-finalization"
        for item in bridge.store.receipts(run.id)["items"]
    )

def test_skill_evidence_distinguishes_assignment_authorization_and_tool_use(
    bridge, fake_transport
) -> None:
    run = _start(bridge)
    fake_transport.spawns_payload = {
        "project_id": run.agentteams_project_id,
        "workers": [
            {
                "worker": "ego-runtime",
                "spawns": [
                    {
                        "session_id": "sub-real-trace",
                        "status": "completed",
                        "spawn": True,
                        "subagent_skills": ["safe-experiment-runner"],
                        "subagent_allowed_tools": ["ego-gpu.launch_experiment"],
                    }
                ],
            }
        ],
    }
    fake_transport.spawn_messages_payload["sub-real-trace"] = {
        "session_id": "sub-real-trace",
        "task": "execute task-live",
        "messages": [
            {
                "seq": 7,
                "kind": "tool_result",
                "role": "assistant",
                "name": "ego-gpu.launch_experiment",
                "content": "accepted",
                "tool_state": "success",
            }
        ],
        "has_more": False,
    }
    payload = bridge.skill_evidence(run.id)
    levels = {item["level"] for item in payload["items"]}
    assert levels == {"DECLARED", "SPAWN_AUTHORIZED", "TOOL_INVOKED"}
    tool = next(item for item in payload["items"] if item["level"] == "TOOL_INVOKED")
    assert tool["tool"] == "ego-gpu.launch_experiment"
    assert tool["message_seq"] == 7
    assert "not_claimed" in payload["claim_boundary"]


def test_verified_benchmark_trace_has_required_real_agentteams_evidence(
    bridge, fake_transport, tmp_path
) -> None:
    run = _start(bridge)
    for node in fake_transport.workflow["nodes"]:
        node["status"] = "delegated"
    bridge.reconcile(run.id)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "APPROVAL",
        "pending_approval": {
            "id": "apr-live-trace",
            "status": "approved",
            "approver": "benchmark-human",
        },
    }
    run = bridge.grant_r2(
        run.id,
        GrantRequest(approval_token="trace-token-not-persisted", idempotency_key="trace-r2-0001"),
    )
    for node in fake_transport.workflow["nodes"]:
        if node["status"] == "pending":
            node["status"] = "delegated"
    bridge.reconcile(run.id)
    fake_transport.complete_all_with_contracts(run)
    run = bridge.reconcile(run.id).run
    assert run.state == RunState.COMPLETED
    assert len(fake_transport.finalization_requests) == 1
    finalization = FinalizeTaskRequest.model_validate(fake_transport.finalization_requests[0])
    assert {item.kind.value for item in finalization.evidence} == {
        "code",
        "config",
        "dataset_manifest",
        "log",
        "metric",
        "trace",
        "review",
    }
    trace_item = next(item for item in finalization.evidence if item.kind.value == "trace")
    assert trace_item.payload.attributes["matrix_raw_messages"]
    receipts = bridge.store.receipts(run.id)
    assert receipts["chain_valid"] is True
    assert any(item["kind"] == "reviewer-decision" for item in receipts["items"])
    assert any(item["receipt_key"] == "ego:live-finalization" for item in receipts["items"])
    assert any(item["receipt_key"] == "agentteams:project-complete" for item in receipts["items"])
    acceptance_index = bridge.acceptance_input_index(run.id)
    assert acceptance_index["inputs_ready_for_assembly"] is True
    assert acceptance_index["bundle_assembled"] is False
    assert acceptance_index["indexed"]["matrix_receipt_ids"]
    assert acceptance_index["indexed"]["reviewer_receipt_ids"]
    assert acceptance_index["indexed"]["metric_artifacts"]
    assert "separate collector" in acceptance_index["assembly_boundary"]

    fake_transport.spawns_payload = {
        "project_id": run.agentteams_project_id,
        "workers": [
            {
                "worker": "ego-runtime",
                "spawns": [
                    {
                        "session_id": "sub-benchmark-live",
                        "status": "completed",
                        "spawn": True,
                        "subagent_skills": ["safe-experiment-runner"],
                        "subagent_allowed_tools": ["ego-gpu.launch_experiment"],
                    }
                ],
            }
        ],
    }
    fake_transport.spawn_messages_payload["sub-benchmark-live"] = {
        "session_id": "sub-benchmark-live",
        "task": "execute the AgentTeams benchmark task",
        "messages": [
            {
                "seq": 9,
                "kind": "tool_result",
                "role": "assistant",
                "name": "ego-gpu.launch_experiment",
                "content": "accepted",
                "tool_state": "success",
            }
        ],
        "has_more": False,
    }
    fake_transport.ego_task = {
        **fake_transport.ego_task,
        "stage": "COMPLETED",
        "decision": "KEEP",
        "current_agent": "research-pi",
        "gate_result": {
            "status": "pass",
            "independent_reviewer": "ego-reviewer",
        },
    }
    snapshots = [
        {
            "state": run.state.value,
            "workflow_sha256": run.checkpoint["last_workflow_sha256"],
            "actions": [],
        }
    ]
    trace = _build_verified_trace(
        bridge,
        run,
        seed=41,
        scenario_id="trace-contract",
        probe={"controller": {"controller": "dev", "kubeMode": "embedded"}},
        snapshots=snapshots,
    )
    assert trace["schema_version"] == TRACE_SCHEMA_VERSION
    assert trace["source"] == "AgentTeams"
    assert trace["execution_mode"] == "real-agentteams"
    assert trace["synthetic"] is False
    assert len(trace["agents"]) >= 3
    assert {principal["kind"] for principal in trace["principals"]} == {
        "bridge",
        "human",
        "ego",
    }
    assert REQUIRED_TRACE_EVENTS <= {event["type"] for event in trace["events"]}
    assert [event["sequence"] for event in trace["events"]] == list(
        range(1, len(trace["events"]) + 1)
    )
    assert all(
        {"sequence", "type", "actor", "task_id", "correlation_id", "payload"} <= event.keys()
        for event in trace["events"]
    )
    assert all(trace["rxp"].values())
    declared_actors = {
        agent["id"] for agent in trace["agents"]
    } | {agent["matrix_user_id"] for agent in trace["agents"]} | {
        principal["id"] for principal in trace["principals"]
    }
    assert {event["actor"] for event in trace["events"]} <= declared_actors
    chain = trace["bridge_event_chain"]
    assert trace["external_origin_status"] == "UNVERIFIED"
    assert chain["external_origin_status"] == "UNVERIFIED"
    assert chain["hash_algorithm"] == "sha256-canonical-json-v1"
    assert chain["total"] == chain["source_ledger_total"] == len(chain["items"])
    assert [item["sequence"] for item in chain["items"]] == list(
        range(1, chain["total"] + 1)
    )
    assert chain["head"] == chain["items"][-1]["event_hash"]
    assert "trace-token-not-persisted" not in json.dumps(chain)
    verified_head, verified_total, verified_events = _verify_bridge_event_chain(
        chain,
        run_id=trace["bridge"]["run_id"],
        task_id=trace["task_id"],
        project_id=trace["project_id"],
        trace_id=trace["trace_id"],
        correlation_id=trace["correlation_id"],
        context_version=trace["context_version"],
    )
    assert verified_head == chain["head"]
    assert verified_total == chain["total"]
    assert len(verified_events) == chain["total"]
    assert trace["bridge"]["api_version"] == "0.3.0"
    assert trace["bridge"]["benchmark_adapter_version"] == "rxp-bench/v1"
    assert trace["official_contract"]["main_commit"] == (
        "223ddc2b8073e4c8b93bcbb15e1d717f196c04d9"
    )
    assert _bind_scenario_proof(trace, "happy_path") is False
    assert {
        "unsafe_action.blocked",
        "effect.committed",
        "replay.run_ids+semantic_digests",
    } <= set(trace["scenario_proof"]["missing_event_types"])
    relative_path, digest = _write_trace(tmp_path, trace)
    assert relative_path == "agentteams-live-trace.json"
    assert digest == hashlib.sha256((tmp_path / relative_path).read_bytes()).hexdigest()
