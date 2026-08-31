from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path

import pytest

from apps.agentteams_bridge.errors import BridgeError
from apps.agentteams_bridge.extensions import (
    ApprovalDisclosure,
    CampaignBinding,
    CanonicalEffect,
    EnforcementMode,
    GuardianDecision,
    RiskAssessment,
    RiskDisposition,
    RiskLevel,
    RiskStage,
    SafetyDecision,
    SafetyVerdict,
    UserMessageMode,
    UserStatusProjection,
    WorkHierarchy,
    WorkLevel,
    WorkNode,
)
from apps.agentteams_bridge.models import BridgeRun, RunState
from apps.agentteams_bridge.store import BridgeStore
from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256
from benchmarks.secure_memory.models import (
    ExecutionPhaseOwner,
    MeasuredConfigurationId,
    RequestClass,
    SignedTaskLease,
    SignedTaskLeaseCore,
)


SHA_A = "a" * 64
SHA_B = "b" * 64


def _run(run_id: str = "bridge_run_extension") -> BridgeRun:
    return BridgeRun(
        id=run_id,
        ego_task_id="ego_task_extension",
        agentteams_project_id="agentteams_project_extension",
        team="ego-researchops",
        trace_id="trace_extension_bridge",
        correlation_id="corr_extension_bridge",
        context_version=1,
        state=RunState.PRE_APPROVAL,
        mode="live",
        objective="Persist append-only campaign extension evidence",
        task_graph=[],
        checkpoint={},
        ack_timeout_seconds=30,
        execution_timeout_seconds=300,
        max_reassignments=2,
    )


def _binding() -> CampaignBinding:
    return CampaignBinding(
        schema_version="agentteams-campaign-envelope/v1",
        campaign_id="campaign-extension",
        configuration_id=MeasuredConfigurationId.D,
        execution_phase_owner=ExecutionPhaseOwner.D,
        problem_id="problem-extension",
        turn=2,
        generation=1,
        manifest_sha256=SHA_A,
        post_selection_extension_sha256=None,
        policy_sha256=SHA_B,
        requirement_ledger_sha256=SHA_A,
        workspace_checkpoint_sha256=SHA_B,
        memory_watermark=7,
    )


def _system_high(effect_sha256: str = SHA_A) -> RiskAssessment:
    return RiskAssessment(
        schema_version="agentteams-risk-assessment/v1",
        effect_sha256=effect_sha256,
        stage=RiskStage.SYSTEM,
        risk_level=RiskLevel.HIGH,
        disposition=RiskDisposition.APPROVAL_REQUIRED,
        reason_codes=("POLICY_WRITE",),
        mandatory_constraint_ids=("constraint.workspace-bound",),
        rule_version="2026-08-31",
        rule_sha256=SHA_B,
        sequence=1,
    )


def _already_verified_lease_bytes() -> bytes:
    core = SignedTaskLeaseCore(
        schema_version="secure-memory-task-lease/v1",
        campaign_id="campaign-extension",
        configuration_id=MeasuredConfigurationId.D,
        execution_phase_owner=ExecutionPhaseOwner.D,
        problem_id="problem-extension",
        turn=2,
        generation=1,
        manifest_sha256=SHA_A,
        post_selection_extension_sha256=None,
        policy_sha256=SHA_B,
        requirement_ledger_sha256=SHA_A,
        workspace_checkpoint_sha256=SHA_B,
        memory_watermark=7,
        project_id="agentteams_project_extension",
        task_id="task-extension",
        worker="ego-runtime",
        matrix_user_id="@ego-runtime:matrix.test",
        role="Runtime",
        stage="EXECUTE",
        allowed_skills=("safe-experiment-runner",),
        allowed_tools=("workspace.write",),
        request_class=RequestClass.MAIN,
        issued_ticket_ids=("ticket-extension",),
        expires_at_sequence=20,
        issuer_id="control",
        key_id="control-key-1",
        issue_sequence=3,
    )
    lease = SignedTaskLease(
        core=core,
        core_sha256=canonical_sha256("task-lease-core", core),
        signature_base64="cHVibGljLXNpZ25hdHVyZQ==",
    )
    return canonical_bytes(lease)


def _guardian_and_safety() -> tuple[GuardianDecision, SafetyDecision]:
    effect_core = {
        "schema_version": "agentteams-canonical-effect/v1",
        "effect_id": "effect-extension",
        "operation": "workspace.write",
        "final_arguments": {"path": "report.md"},
        "target": "workspace/report.md",
        "affected_scope": ("project:agentteams_project_extension",),
        "project_id": "agentteams_project_extension",
        "task_id": "task-extension",
        "workspace_checkpoint_sha256": SHA_B,
        "policy_sha256": SHA_B,
        "reversibility": "REVERSIBLE",
        "recovery_plan": "restore the bound checkpoint",
    }
    effect = CanonicalEffect(
        **effect_core,
        effect_sha256=canonical_sha256("agentteams-canonical-effect", effect_core),
    )
    system = _system_high(effect.effect_sha256)
    guardian_assessment = RiskAssessment(
        schema_version="agentteams-risk-assessment/v1",
        effect_sha256=effect.effect_sha256,
        stage=RiskStage.GUARDIAN,
        risk_level=RiskLevel.HIGH,
        disposition=RiskDisposition.APPROVAL_REQUIRED,
        reason_codes=("POLICY_WRITE",),
        mandatory_constraint_ids=("constraint.workspace-bound",),
        rule_version="2026-08-31",
        rule_sha256=SHA_B,
        sequence=2,
    )
    guardian_core = {
        "schema_version": "agentteams-guardian-decision/v1",
        "effect_sha256": effect.effect_sha256,
        "system_assessment": system,
        "guardian_assessment": guardian_assessment,
        "enforcement_mode": EnforcementMode.ENFORCING,
        "disposition": RiskDisposition.APPROVAL_REQUIRED,
    }
    guardian = GuardianDecision(
        **guardian_core,
        decision_sha256=canonical_sha256("agentteams-guardian-decision", guardian_core),
    )
    disclosure = ApprovalDisclosure(
        schema_version="agentteams-approval-disclosure/v1",
        effect_sha256=effect.effect_sha256,
        safe_arguments=effect.final_arguments,
        target=effect.target,
        affected_scope=effect.affected_scope,
        reason_codes=("POLICY_WRITE",),
        recovery_plan=effect.recovery_plan,
        expires_at_sequence=20,
        allowed_responses=("APPROVE", "DENY"),
    )
    safety_core = {
        "schema_version": "agentteams-safety-decision/v1",
        "effect": effect,
        "guardian_decision": guardian,
        "verdict": SafetyVerdict.APPROVAL_REQUIRED,
        "approval_pending": True,
        "approval_disclosure": disclosure,
    }
    safety = SafetyDecision(
        **safety_core,
        decision_sha256=canonical_sha256("agentteams-safety-decision", safety_core),
    )
    return guardian, safety


def _approval_projection(
    guardian: GuardianDecision,
    safety: SafetyDecision,
    source_event_ids: tuple[str, ...],
) -> UserStatusProjection:
    hierarchy = WorkHierarchy(
        schema_version="agentteams-work-hierarchy/v1",
        current_node_id="agentteams_project_extension",
        direct_child_ids=("task-extension",),
        nodes=(
            WorkNode(
                node_id="agentteams_project_extension",
                level=WorkLevel.PROJECT,
                parent_id="tenant-extension",
                child_ids=("task-extension",),
                order=0,
            ),
            WorkNode(
                node_id="task-extension",
                level=WorkLevel.TASK,
                parent_id="agentteams_project_extension",
                child_ids=(),
                order=0,
            ),
        ),
    )
    projection_core = {
        "schema_version": "agentteams-user-status-projection/v1",
        "tenant_id": "tenant-extension",
        "project_id": "agentteams_project_extension",
        "task_id": "task-extension",
        "mode": UserMessageMode.APPROVAL,
        "hierarchy": hierarchy,
        "visible_node_ids": ("agentteams_project_extension", "task-extension"),
        "override_node_ids": (),
        "status_text": "Approval is required for the exact workspace effect.",
        "guardian_decision": guardian,
        "guardian_decision_sha256": guardian.decision_sha256,
        "safety_decision": safety,
        "safety_decision_sha256": safety.decision_sha256,
        "approval_disclosure": safety.approval_disclosure,
        "explained_terms": (("checkpoint", "A bound snapshot of workspace state."),),
        "source_event_ids": source_event_ids,
    }
    return UserStatusProjection(
        **projection_core,
        projection_sha256=canonical_sha256(
            "agentteams-user-status-projection", projection_core
        ),
    )


def _populate_complete_authority(store: BridgeStore, run: BridgeRun) -> None:
    store.bind_campaign(run.id, _binding())
    store.store_signed_task_lease(
        run.id,
        already_verified_signed_payload=_already_verified_lease_bytes(),
        idempotency_key="lease:task-extension",
    )
    evaluator_bytes = canonical_bytes(
        {
            "schema_version": "sealed-evaluator-binding/v1",
            "decision_sha256": SHA_A,
            "task_id": "task-extension",
        }
    )
    store.bind_sealed_evaluation(
        run.id,
        binding_id="evaluation-extension",
        task_id="task-extension",
        already_verified_signed_payload=evaluator_bytes,
        signature_base64="ZXZhbHVhdG9yLXNpZ25hdHVyZQ==",
        key_id="evaluator-key-1",
        issuer_id="sealed-evaluator",
        idempotency_key="evaluation:task-extension",
    )
    guardian, safety = _guardian_and_safety()
    effect_event = store.append_extension_event(
        run.id,
        event_type="CANONICAL_EFFECT",
        event=safety.effect,
        idempotency_key="effect:1",
        memory_watermark=7,
    )
    system_event = store.append_extension_event(
        run.id,
        event_type="SYSTEM_RISK_ASSESSMENT",
        event=_system_high(safety.effect.effect_sha256),
        idempotency_key="risk:system:1",
        memory_watermark=7,
    )
    guardian_event = store.append_extension_event(
        run.id,
        event_type="GUARDIAN_DECISION",
        event=guardian,
        idempotency_key="guardian:1",
        memory_watermark=7,
    )
    safety_event = store.append_extension_event(
        run.id,
        event_type="SAFETY_DECISION",
        event=safety,
        idempotency_key="safety:1",
        memory_watermark=7,
    )
    projection = _approval_projection(
        guardian,
        safety,
        tuple(
            sorted(
                (
                    effect_event["event_id"],
                    system_event["event_id"],
                    guardian_event["event_id"],
                    safety_event["event_id"],
                )
            )
        ),
    )
    store.append_extension_event(
        run.id,
        event_type="USER_STATUS_PROJECTION",
        event=projection,
        idempotency_key="projection:1",
        memory_watermark=7,
    )


def test_sqlite_binds_and_reads_one_immutable_campaign_authority() -> None:
    store = BridgeStore(":memory:")
    run = store.create_run(_run())
    binding = _binding()

    stored = store.bind_campaign(run.id, binding)

    assert stored["binding"] == binding.model_dump(mode="json")
    assert store.campaign_binding(
        run.id,
        project_id=run.agentteams_project_id,
        configuration_id="D",
    ) == stored


def test_sqlite_extension_event_is_canonical_idempotent_and_root_verified() -> None:
    store = BridgeStore(":memory:")
    run = store.create_run(_run())
    store.bind_campaign(run.id, _binding())
    assessment = _system_high()

    first = store.append_extension_event(
        run.id,
        event_type="SYSTEM_RISK_ASSESSMENT",
        event=assessment,
        idempotency_key="risk:system:1",
        memory_watermark=7,
    )
    replay = store.append_extension_event(
        run.id,
        event_type="SYSTEM_RISK_ASSESSMENT",
        event=assessment,
        idempotency_key="risk:system:1",
        memory_watermark=7,
    )

    assert replay == {**first, "idempotent_replay": True}
    persisted = store.extension_events(
        run.id,
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )
    assert persisted["total"] == 1
    assert persisted["chain_valid"] is True
    assert persisted["root_hash"] == first["event_hash"]
    assert store.verify_extension_root(
        run.id,
        expected_root_hash=first["event_hash"],
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )

    changed = assessment.model_copy(update={"sequence": 2})
    with pytest.raises(BridgeError) as raised:
        store.append_extension_event(
            run.id,
            event_type="SYSTEM_RISK_ASSESSMENT",
            event=changed,
            idempotency_key="risk:system:1",
            memory_watermark=7,
        )
    assert raised.value.code == "idempotency_conflict"


def test_sqlite_persists_already_verified_lease_and_evaluator_admissions() -> None:
    store = BridgeStore(":memory:")
    run = store.create_run(_run())
    store.bind_campaign(run.id, _binding())
    lease_bytes = _already_verified_lease_bytes()

    lease = store.store_signed_task_lease(
        run.id,
        already_verified_signed_payload=lease_bytes,
        idempotency_key="lease:task-extension",
    )
    loaded_lease = store.task_lease(
        run.id,
        task_id="task-extension",
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )

    assert loaded_lease == lease
    assert lease["canonical_signed_payload"] == lease_bytes
    assert lease["payload_sha256"] == hashlib.sha256(lease_bytes).hexdigest()
    assert lease["issuer_id"] == "control"
    assert lease["key_id"] == "control-key-1"
    assert lease["signature_base64"] == "cHVibGljLXNpZ25hdHVyZQ=="

    evaluator_bytes = canonical_bytes(
        {
            "schema_version": "sealed-evaluator-binding/v1",
            "decision_sha256": SHA_A,
            "task_id": "task-extension",
        }
    )
    evaluator = store.bind_sealed_evaluation(
        run.id,
        binding_id="evaluation-extension",
        task_id="task-extension",
        already_verified_signed_payload=evaluator_bytes,
        signature_base64="ZXZhbHVhdG9yLXNpZ25hdHVyZQ==",
        key_id="evaluator-key-1",
        issuer_id="sealed-evaluator",
        idempotency_key="evaluation:task-extension",
    )

    assert store.evaluator_binding(
        run.id,
        binding_id="evaluation-extension",
        project_id=run.agentteams_project_id,
        configuration_id="D",
    ) == evaluator
    assert evaluator["canonical_signed_payload"] == evaluator_bytes
    assert evaluator["payload_sha256"] == hashlib.sha256(evaluator_bytes).hexdigest()
    assert store.extension_events(
        run.id,
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )["total"] == 2


def test_sqlite_enforces_guardian_safety_and_projection_admission_order() -> None:
    store = BridgeStore(":memory:")
    run = store.create_run(_run())
    store.bind_campaign(run.id, _binding())
    store.store_signed_task_lease(
        run.id,
        already_verified_signed_payload=_already_verified_lease_bytes(),
        idempotency_key="lease:task-extension",
    )
    guardian, safety = _guardian_and_safety()
    store.append_extension_event(
        run.id,
        event_type="CANONICAL_EFFECT",
        event=safety.effect,
        idempotency_key="effect:1",
        memory_watermark=7,
    )

    with pytest.raises(BridgeError) as early_guardian:
        store.append_extension_event(
            run.id,
            event_type="GUARDIAN_DECISION",
            event=guardian,
            idempotency_key="guardian:early",
            memory_watermark=7,
        )
    assert early_guardian.value.code == "guardian_order_invalid"

    system_event = store.append_extension_event(
        run.id,
        event_type="SYSTEM_RISK_ASSESSMENT",
        event=_system_high(safety.effect.effect_sha256),
        idempotency_key="risk:system:1",
        memory_watermark=7,
    )
    guardian_event = store.append_extension_event(
        run.id,
        event_type="GUARDIAN_DECISION",
        event=guardian,
        idempotency_key="guardian:1",
        memory_watermark=7,
    )
    safety_event = store.append_extension_event(
        run.id,
        event_type="SAFETY_DECISION",
        event=safety,
        idempotency_key="safety:1",
        memory_watermark=7,
    )

    invalid_projection = _approval_projection(
        guardian,
        safety,
        tuple(sorted((system_event["event_id"], "xevt_unadmitted"))),
    )
    with pytest.raises(BridgeError) as unadmitted:
        store.append_extension_event(
            run.id,
            event_type="USER_STATUS_PROJECTION",
            event=invalid_projection,
            idempotency_key="projection:invalid",
            memory_watermark=7,
        )
    assert unadmitted.value.code == "projection_source_unadmitted"

    source_ids = tuple(
        sorted(
            (
                system_event["event_id"],
                guardian_event["event_id"],
                safety_event["event_id"],
            )
        )
    )
    projection = _approval_projection(guardian, safety, source_ids)
    projected = store.append_extension_event(
        run.id,
        event_type="USER_STATUS_PROJECTION",
        event=projection,
        idempotency_key="projection:1",
        memory_watermark=7,
    )

    assert projected["sequence"] == 6
    assert projected["event"]["projection_sha256"] == projection.projection_sha256


def test_sqlite_fresh_restart_replays_the_complete_extension_authority(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "bridge-extension.sqlite3"
    first = BridgeStore(str(database_path))
    run = first.create_run(_run())
    _populate_complete_authority(first, run)

    before = first.replay_extension_authority(
        run.id,
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )
    restarted = BridgeStore(str(database_path))
    after = restarted.replay_extension_authority(
        run.id,
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )

    assert after == before
    assert after["events"]["total"] == 7
    assert after["events"]["chain_valid"] is True
    assert len(after["task_leases"]) == 1
    assert len(after["evaluator_bindings"]) == 1
    assert len(after["guardian_decisions"]) == 1
    assert len(after["safety_decisions"]) == 1
    assert after["projection"]["event_type"] == "USER_STATUS_PROJECTION"

    for project_id, configuration_id in (
        ("another-project", "D"),
        (run.agentteams_project_id, "E"),
    ):
        with pytest.raises(BridgeError) as mismatch:
            restarted.replay_extension_authority(
                run.id,
                project_id=project_id,
                configuration_id=configuration_id,
            )
        assert mismatch.value.code == "campaign_binding_not_found"


def test_sqlite_legacy_run_is_readable_and_extension_history_is_immutable(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "bridge-legacy.sqlite3"
    first = BridgeStore(str(database_path))
    run = first.create_run(_run())
    assert BridgeStore(str(database_path)).get_run(run.id) == run

    _populate_complete_authority(first, run)
    immutable_statements = (
        ("UPDATE bridge_runs SET campaign_id='changed' WHERE id=?", (run.id,)),
        (
            "UPDATE bridge_extension_events SET event_type='changed' WHERE run_id=?",
            (run.id,),
        ),
        ("DELETE FROM bridge_task_leases WHERE run_id=?", (run.id,)),
        ("DELETE FROM bridge_evaluator_bindings WHERE run_id=?", (run.id,)),
    )
    for statement, parameters in immutable_statements:
        with pytest.raises(sqlite3.IntegrityError):
            with first._connection:
                first._connection.execute(statement, parameters)

    expected_root = first.extension_events(
        run.id,
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )["root_hash"]
    with first._connection:
        first._connection.execute("DROP TRIGGER bridge_extension_events_no_update")
        first._connection.execute(
            """
            UPDATE bridge_extension_events SET canonical_payload=?
            WHERE run_id=? AND sequence=1
            """,
            (b"{}", run.id),
        )
    assert not first.verify_extension_root(
        run.id,
        expected_root_hash=expected_root,
        project_id=run.agentteams_project_id,
        configuration_id="D",
    )
