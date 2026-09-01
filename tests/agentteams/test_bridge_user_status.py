from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import pytest

from apps.agentteams_bridge.extensions import (
    ApprovalDisclosure,
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
    WorkHierarchy,
    WorkLevel,
    WorkNode,
)
from benchmarks.secure_memory.canonical import canonical_sha256


SHA_A = "a" * 64
SHA_B = "b" * 64


def _status_module() -> ModuleType:
    try:
        return importlib.import_module("apps.agentteams_bridge.extensions.user_status")
    except ModuleNotFoundError:
        pytest.fail("the deterministic user-status projector is missing")


def _project_hierarchy() -> WorkHierarchy:
    return WorkHierarchy(
        schema_version="agentteams-work-hierarchy/v1",
        current_node_id="project-1",
        direct_child_ids=("task-1", "task-2"),
        nodes=(
            WorkNode(
                node_id="project-1",
                level=WorkLevel.PROJECT,
                parent_id="tenant-1",
                child_ids=("task-1", "task-2"),
                order=0,
            ),
            WorkNode(
                node_id="task-1",
                level=WorkLevel.TASK,
                parent_id="project-1",
                child_ids=("subtask-1",),
                order=0,
            ),
            WorkNode(
                node_id="task-2",
                level=WorkLevel.TASK,
                parent_id="project-1",
                child_ids=(),
                order=1,
            ),
            WorkNode(
                node_id="subtask-1",
                level=WorkLevel.SUBTASK,
                parent_id="task-1",
                child_ids=(),
                order=0,
            ),
        ),
    )


def _task_hierarchy() -> WorkHierarchy:
    return WorkHierarchy(
        schema_version="agentteams-work-hierarchy/v1",
        current_node_id="task-1",
        direct_child_ids=("subtask-1",),
        nodes=_project_hierarchy().nodes,
    )


def _event(
    event_id: str,
    node_id: str,
    *,
    event_type: str = "PROGRESS",
    state_code: str = "ACTIVE",
    sequence: int = 1,
    specialist_terms: tuple[str, ...] = (),
    **overrides: Any,
) -> Any:
    module = _status_module()
    values: dict[str, Any] = {
        "schema_version": "agentteams-admitted-status-event/v1",
        "event_id": event_id,
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "node_id": node_id,
        "event_type": event_type,
        "state_code": state_code,
        "sequence": sequence,
        "specialist_terms": specialist_terms,
        "admitted": True,
        "guardian_decision_sha256": None,
        "safety_decision_sha256": None,
    }
    values.update(overrides)
    return module.AdmittedStatusEvent.model_validate(values)


def _approval_chain(
    *, safe_arguments: dict[str, Any] | None = None
) -> tuple[GuardianDecision, SafetyDecision, ApprovalDisclosure]:
    effect_core: dict[str, Any] = {
        "schema_version": "agentteams-canonical-effect/v1",
        "effect_id": "effect-1",
        "operation": "workspace.write",
        "final_arguments": safe_arguments or {"path": "report.md"},
        "target": "workspace/report.md",
        "affected_scope": ("project:project-1",),
        "project_id": "project-1",
        "task_id": "task-1",
        "workspace_checkpoint_sha256": SHA_A,
        "policy_sha256": SHA_B,
        "reversibility": "REVERSIBLE",
        "recovery_plan": "restore the bound checkpoint",
    }
    effect = CanonicalEffect(
        **effect_core,
        effect_sha256=canonical_sha256("agentteams-canonical-effect", effect_core),
    )
    system = RiskAssessment(
        schema_version="agentteams-risk-assessment/v1",
        effect_sha256=effect.effect_sha256,
        stage=RiskStage.SYSTEM,
        risk_level=RiskLevel.HIGH,
        disposition=RiskDisposition.APPROVAL_REQUIRED,
        reason_codes=("POLICY_WRITE",),
        mandatory_constraint_ids=("constraint.workspace-bound",),
        rule_version="2026-08-31",
        rule_sha256=SHA_A,
        sequence=7,
    )
    guardian_assessment = system.model_copy(
        update={"stage": RiskStage.GUARDIAN, "sequence": 8}
    )
    guardian_core: dict[str, Any] = {
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
        expires_at_sequence=11,
        allowed_responses=("APPROVE", "DENY"),
    )
    safety_core: dict[str, Any] = {
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
    return guardian, safety, disclosure


def _project(
    events: tuple[Any, ...],
    *,
    mode: UserMessageMode = UserMessageMode.PROGRESS,
    hierarchy: WorkHierarchy | None = None,
    locale: str = "en-US",
    guardian: GuardianDecision | None = None,
    safety: SafetyDecision | None = None,
    disclosure: ApprovalDisclosure | None = None,
) -> Any:
    module = _status_module()
    return module.project_user_status(
        tenant_id="tenant-1",
        project_id="project-1",
        task_id="task-1",
        hierarchy=hierarchy or _project_hierarchy(),
        source_events=events,
        requested_mode=mode,
        locale=locale,
        guardian_decision=guardian,
        safety_decision=safety,
        approval_disclosure=disclosure,
    )


def test_progress_shows_only_current_and_direct_children_not_grandchildren() -> None:
    projection = _project(
        (
            _event("event-project", "project-1"),
            _event("event-task-1", "task-1", state_code="COMPLETED"),
            _event("event-task-2", "task-2", state_code="PENDING"),
            _event(
                "event-secret-depth",
                "subtask-1",
                state_code="FAILED",
                sequence=9,
            ),
        )
    )

    assert projection.visible_node_ids == ("project-1", "task-1", "task-2")
    assert projection.override_node_ids == ()
    assert "subtask-1" not in projection.status_text
    assert "event-secret-depth" not in projection.status_text
    assert "task-1" in projection.status_text
    assert "task-2" in projection.status_text


def test_detail_drills_down_exactly_one_level_and_emits_next_reference() -> None:
    projection = _project(
        (
            _event("event-task", "task-1"),
            _event("event-subtask", "subtask-1", state_code="BLOCKED"),
            _event("event-sibling", "task-2", state_code="COMPLETED"),
        ),
        mode=UserMessageMode.DETAIL,
        hierarchy=_task_hierarchy(),
    )

    assert projection.visible_node_ids == ("task-1", "subtask-1")
    assert "subtask-1" in projection.status_text
    assert "task-2" not in projection.status_text
    assert "DETAIL" in projection.status_text


def test_risk_event_overrides_depth_and_is_never_suppressed() -> None:
    projection = _project(
        (
            _event("event-project", "project-1"),
            _event(
                "event-risk",
                "subtask-1",
                event_type="RISK",
                state_code="BLOCKED",
                sequence=10,
                specialist_terms=("Evidence Gate",),
            ),
        ),
        mode=UserMessageMode.RISK,
    )

    assert projection.override_node_ids == ("subtask-1",)
    assert projection.visible_node_ids == (
        "project-1",
        "task-1",
        "task-2",
        "subtask-1",
    )
    assert "subtask-1" in projection.status_text
    assert "Evidence Gate (" in projection.status_text


def test_risk_at_direct_child_names_the_exact_attention_location() -> None:
    projection = _project(
        (
            _event("event-project", "project-1"),
            _event(
                "event-risk",
                "task-1",
                event_type="RISK",
                state_code="BLOCKED",
            ),
        ),
        mode=UserMessageMode.RISK,
    )

    assert projection.override_node_ids == ()
    assert "attention at task-1" in projection.status_text


@pytest.mark.parametrize("mode,event_type", [(UserMessageMode.APPROVAL, "APPROVAL"), (UserMessageMode.SECURITY, "SECURITY")])
def test_safety_modes_override_depth_with_exact_bound_decisions(
    mode: UserMessageMode, event_type: str
) -> None:
    guardian, safety, disclosure = _approval_chain()
    event = _event(
        "event-safety",
        "subtask-1",
        event_type=event_type,
        state_code="BLOCKED",
        sequence=20,
        specialist_terms=("Guardian", "Trace"),
        guardian_decision_sha256=guardian.decision_sha256,
        safety_decision_sha256=safety.decision_sha256,
    )

    projection = _project(
        (event,),
        mode=mode,
        guardian=guardian,
        safety=safety,
        disclosure=disclosure if mode is UserMessageMode.APPROVAL else None,
    )

    assert projection.override_node_ids == ("subtask-1",)
    assert projection.guardian_decision_sha256 == guardian.decision_sha256
    assert projection.safety_decision_sha256 == safety.decision_sha256
    assert "Guardian (" in projection.status_text
    assert "Trace (" in projection.status_text


def test_approval_renders_exact_safe_material_and_literal_choices() -> None:
    guardian, safety, disclosure = _approval_chain(
        safe_arguments={"path": "report.md", "overwrite": False}
    )
    projection = _project(
        (
            _event(
                "event-approval",
                "subtask-1",
                event_type="APPROVAL",
                state_code="BLOCKED",
                guardian_decision_sha256=guardian.decision_sha256,
                safety_decision_sha256=safety.decision_sha256,
            ),
        ),
        mode=UserMessageMode.APPROVAL,
        guardian=guardian,
        safety=safety,
        disclosure=disclosure,
    )

    assert '"overwrite":false' in projection.status_text
    assert '"path":"report.md"' in projection.status_text
    assert "workspace/report.md" in projection.status_text
    assert "POLICY_WRITE" in projection.status_text
    assert "restore the bound checkpoint" in projection.status_text
    assert "11" in projection.status_text
    assert "APPROVE|DENY" in projection.status_text


def test_approval_rejects_stale_event_safety_binding() -> None:
    guardian, safety, disclosure = _approval_chain()
    stale = _event(
        "event-approval",
        "subtask-1",
        event_type="APPROVAL",
        state_code="BLOCKED",
        guardian_decision_sha256=SHA_A,
        safety_decision_sha256=safety.decision_sha256,
    )

    with pytest.raises(ValueError, match="stale|Guardian|digest|binding"):
        _project(
            (stale,),
            mode=UserMessageMode.APPROVAL,
            guardian=guardian,
            safety=safety,
            disclosure=disclosure,
        )


@pytest.mark.parametrize(
    "safe_arguments",
    [
        {"password": "hunter2"},
        {"headers": {"Authorization": "Bearer abc.def.ghi"}},
        {"api_key": "sk-secret-material"},
        {"client_secret": "not-safe-to-display"},
    ],
)
def test_approval_rejects_secret_bearing_arguments(
    safe_arguments: dict[str, Any]
) -> None:
    guardian, safety, disclosure = _approval_chain(safe_arguments=safe_arguments)
    event = _event(
        "event-approval",
        "subtask-1",
        event_type="APPROVAL",
        guardian_decision_sha256=guardian.decision_sha256,
        safety_decision_sha256=safety.decision_sha256,
    )

    with pytest.raises(ValueError, match="secret|credential|safe"):
        _project(
            (event,),
            mode=UserMessageMode.APPROVAL,
            guardian=guardian,
            safety=safety,
            disclosure=disclosure,
        )


def test_security_rejects_secret_bearing_bound_effect() -> None:
    guardian, safety, _ = _approval_chain(
        safe_arguments={"headers": {"Authorization": "Bearer abc.def.ghi"}}
    )
    event = _event(
        "event-security",
        "subtask-1",
        event_type="SECURITY",
        state_code="BLOCKED",
        guardian_decision_sha256=guardian.decision_sha256,
        safety_decision_sha256=safety.decision_sha256,
    )

    with pytest.raises(ValueError, match="secret|credential|safe"):
        _project(
            (event,),
            mode=UserMessageMode.SECURITY,
            guardian=guardian,
            safety=safety,
        )


def test_projection_fails_closed_for_missing_glossary_term() -> None:
    with pytest.raises(ValueError, match="glossary|term"):
        _project(
            (
                _event(
                    "event-unknown-term",
                    "task-1",
                    specialist_terms=("QuantumFluxDB",),
                ),
            )
        )


@pytest.mark.parametrize("locale", ["en-US", "zh-CN"])
def test_projection_explains_fixed_specialist_terms_on_first_use(locale: str) -> None:
    projection = _project(
        (
            _event(
                "event-terms",
                "task-1",
                specialist_terms=("CAS", "Guardian", "RLS", "Trace"),
            ),
        ),
        locale=locale,
    )

    assert tuple(term for term, _ in projection.explained_terms) == (
        "CAS",
        "Guardian",
        "RLS",
        "Trace",
    )
    for term, explanation in projection.explained_terms:
        assert f"{term} ({explanation})" in projection.status_text


def test_projection_rejects_unadmitted_cross_scope_and_agent_free_text_events() -> None:
    module = _status_module()
    base = _event("event-1", "task-1").model_dump(mode="python")

    for update in (
        {"admitted": False},
        {"tenant_id": "tenant-other"},
        {"project_id": "project-other"},
        {"status_text": "Agent says everything passed."},
    ):
        with pytest.raises(ValueError):
            event = module.AdmittedStatusEvent.model_validate({**base, **update})
            _project((event,))


def test_projection_sorts_source_ids_and_is_deterministic() -> None:
    events = (
        _event("event-z", "task-2", sequence=1),
        _event("event-a", "project-1", sequence=2),
        _event("event-m", "task-1", sequence=3),
    )

    first = _project(events)
    second = _project(tuple(reversed(events)))

    assert first == second
    assert first.source_event_ids == ("event-a", "event-m", "event-z")
    assert len(first.projection_sha256) == 64


def test_projection_rejects_duplicate_event_ids_and_unknown_nodes() -> None:
    with pytest.raises(ValueError, match="duplicate|event"):
        _project(
            (
                _event("event-same", "task-1"),
                _event("event-same", "task-2"),
            )
        )
    with pytest.raises(ValueError, match="node|hierarchy"):
        _project((_event("event-unknown", "task-other"),))
