from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from apps.agentteams_bridge.extensions import (
    ApprovalDisclosure,
    AttentionFactRef,
    AttentionPacket,
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
from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import ExecutionPhaseOwner, MeasuredConfigurationId


SHA = "a" * 64
OTHER_SHA = "b" * 64


def _effect_payload(**overrides: Any) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "schema_version": "agentteams-canonical-effect/v1",
        "effect_id": "effect-1",
        "operation": "workspace.write",
        "final_arguments": {
            "metadata": {"attempt": 1, "reviewed": True},
            "path": "report.md",
            "tags": ["safe", None],
        },
        "target": "workspace/report.md",
        "affected_scope": ("project:project-1", "task:task-1"),
        "project_id": "project-1",
        "task_id": "task-1",
        "workspace_checkpoint_sha256": SHA,
        "policy_sha256": OTHER_SHA,
        "reversibility": "REVERSIBLE",
        "recovery_plan": "restore the bound workspace checkpoint",
    }
    values.update(overrides)
    values["effect_sha256"] = canonical_sha256("agentteams-canonical-effect", values)
    return values


def _assessment_payload(
    effect_sha256: str,
    *,
    stage: RiskStage,
    risk_level: RiskLevel,
    disposition: RiskDisposition,
    reason_codes: tuple[str, ...] = ("POLICY_WRITE",),
    mandatory_constraint_ids: tuple[str, ...] = ("constraint.workspace-bound",),
) -> Dict[str, Any]:
    return {
        "schema_version": "agentteams-risk-assessment/v1",
        "effect_sha256": effect_sha256,
        "stage": stage,
        "risk_level": risk_level,
        "disposition": disposition,
        "reason_codes": reason_codes,
        "mandatory_constraint_ids": mandatory_constraint_ids,
        "rule_version": "2026-08-31",
        "rule_sha256": SHA,
        "sequence": 7,
    }


def _guardian_payload(
    effect_sha256: str,
    *,
    system_level: RiskLevel = RiskLevel.HIGH,
    guardian_assessment: Optional[Dict[str, Any]] = None,
    include_guardian: bool = True,
) -> Dict[str, Any]:
    disposition = (
        RiskDisposition.APPROVAL_REQUIRED
        if system_level is RiskLevel.HIGH
        else RiskDisposition.ALLOW
    )
    system = _assessment_payload(
        effect_sha256,
        stage=RiskStage.SYSTEM,
        risk_level=system_level,
        disposition=disposition,
    )
    if guardian_assessment is None and include_guardian:
        guardian_assessment = _assessment_payload(
            effect_sha256,
            stage=RiskStage.GUARDIAN,
            risk_level=system_level,
            disposition=disposition,
        )
    values: Dict[str, Any] = {
        "schema_version": "agentteams-guardian-decision/v1",
        "effect_sha256": effect_sha256,
        "system_assessment": system,
        "guardian_assessment": guardian_assessment if include_guardian else None,
        "enforcement_mode": EnforcementMode.ENFORCING,
        "disposition": disposition,
    }
    values["decision_sha256"] = canonical_sha256("agentteams-guardian-decision", values)
    return values


def _disclosure_payload(effect: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": "agentteams-approval-disclosure/v1",
        "effect_sha256": effect["effect_sha256"],
        "safe_arguments": effect["final_arguments"],
        "target": effect["target"],
        "affected_scope": effect["affected_scope"],
        "reason_codes": ("POLICY_WRITE",),
        "recovery_plan": effect["recovery_plan"],
        "expires_at_sequence": 11,
        "allowed_responses": ("APPROVE", "DENY"),
    }


def _safety_payload(effect: Dict[str, Any], guardian: Dict[str, Any]) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "schema_version": "agentteams-safety-decision/v1",
        "effect": effect,
        "guardian_decision": guardian,
        "verdict": SafetyVerdict.APPROVAL_REQUIRED,
        "approval_pending": True,
        "approval_disclosure": _disclosure_payload(effect),
    }
    values["decision_sha256"] = canonical_sha256("agentteams-safety-decision", values)
    return values


def _fact_ref_payload(**overrides: Any) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "fact_sha256": SHA,
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "component": "bridge",
        "version": "v1",
        "outcome": "verified",
        "origin": "trusted-memory",
        "lifecycle": "VALIDATED",
        "relevance_score_basis_points": 9500,
        "evidence_watermark": 12,
    }
    values.update(overrides)
    return values


def _attention_packet_payload(**overrides: Any) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "schema_version": "agentteams-attention-packet/v1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "task_id": "task-1",
        "turn": 2,
        "generation": 1,
        "requirement_ledger_sha256": SHA,
        "workspace_checkpoint_sha256": OTHER_SHA,
        "policy_sha256": SHA,
        "memory_watermark": 12,
        "current_requirement_text": "Keep the report grounded in accepted evidence.",
        "unresolved_failure_ids": ("failure.known",),
        "mandatory_policy_constraint_ids": ("constraint.workspace-bound",),
        "eligible_fact_refs": (_fact_ref_payload(),),
        "explicit_exclusions": ("fact.revoked",),
        "token_budget": 800,
        "estimated_tokens": 320,
    }
    values.update(overrides)
    source_core = dict(values)
    values["source_context_sha256"] = canonical_sha256(
        "agentteams-attention-source-context", source_core
    )
    values["packet_sha256"] = canonical_sha256("agentteams-attention-packet", values)
    return values


def _hierarchy_payload() -> Dict[str, Any]:
    return {
        "schema_version": "agentteams-work-hierarchy/v1",
        "current_node_id": "project-1",
        "direct_child_ids": ("task-1", "task-2"),
        "nodes": (
            {
                "node_id": "project-1",
                "level": WorkLevel.PROJECT,
                "parent_id": "tenant-1",
                "child_ids": ("task-1", "task-2"),
                "order": 0,
            },
            {
                "node_id": "task-1",
                "level": WorkLevel.TASK,
                "parent_id": "project-1",
                "child_ids": ("subtask-1",),
                "order": 0,
            },
            {
                "node_id": "task-2",
                "level": WorkLevel.TASK,
                "parent_id": "project-1",
                "child_ids": (),
                "order": 1,
            },
            {
                "node_id": "subtask-1",
                "level": WorkLevel.SUBTASK,
                "parent_id": "task-1",
                "child_ids": (),
                "order": 0,
            },
        ),
    }


def _projection_payload(
    *,
    mode: UserMessageMode = UserMessageMode.PROGRESS,
    visible_node_ids: tuple[str, ...] = ("project-1", "task-1", "task-2"),
    override_node_ids: tuple[str, ...] = (),
    guardian: Optional[Dict[str, Any]] = None,
    safety: Optional[Dict[str, Any]] = None,
    disclosure: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "schema_version": "agentteams-user-status-projection/v1",
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "task_id": "task-1",
        "mode": mode,
        "hierarchy": _hierarchy_payload(),
        "visible_node_ids": visible_node_ids,
        "override_node_ids": override_node_ids,
        "status_text": "One task is active and one is queued.",
        "guardian_decision": guardian,
        "guardian_decision_sha256": guardian["decision_sha256"] if guardian else None,
        "safety_decision": safety,
        "safety_decision_sha256": safety["decision_sha256"] if safety else None,
        "approval_disclosure": disclosure,
        "explained_terms": (
            ("checkpoint", "A bound snapshot of workspace state."),
            ("watermark", "The accepted trusted-memory sequence."),
        ),
        "source_event_ids": ("event-1", "event-2"),
    }
    values["projection_sha256"] = canonical_sha256(
        "agentteams-user-status-projection", values
    )
    return values


def test_campaign_binding_rejects_an_owner_configuration_mismatch() -> None:
    payload = {
        "schema_version": "agentteams-campaign-envelope/v1",
        "campaign_id": "campaign-1",
        "configuration_id": MeasuredConfigurationId.D,
        "execution_phase_owner": ExecutionPhaseOwner.C,
        "problem_id": "problem-1",
        "turn": 1,
        "generation": 1,
        "manifest_sha256": SHA,
        "post_selection_extension_sha256": None,
        "policy_sha256": SHA,
        "requirement_ledger_sha256": SHA,
        "workspace_checkpoint_sha256": SHA,
        "memory_watermark": 0,
    }

    try:
        CampaignBinding.model_validate(payload)
    except ValueError as exc:
        assert "configuration" in str(exc)
    else:
        raise AssertionError("owner/configuration mismatch was accepted")


def test_canonical_effect_rejects_unknown_fields_and_unsafe_json() -> None:
    unknown = _effect_payload()
    unknown["bearer_token"] = "must-not-pass"
    with pytest.raises(ValueError, match="bearer_token"):
        CanonicalEffect.model_validate(unknown)

    unsafe = _effect_payload()
    unsafe["final_arguments"] = {"weight": float("nan")}
    with pytest.raises(ValueError, match="finite|NaN|canonical JSON"):
        CanonicalEffect.model_validate(unsafe)


@pytest.mark.parametrize(
    ("field", "values"),
    [
        ("reason_codes", ("Z_REASON", "A_REASON")),
        ("mandatory_constraint_ids", ("constraint.a", "constraint.a")),
    ],
)
def test_risk_assessment_rejects_unsorted_or_duplicate_ids(
    field: str, values: tuple[str, ...]
) -> None:
    effect = CanonicalEffect.model_validate(_effect_payload())
    payload = _assessment_payload(
        effect.effect_sha256,
        stage=RiskStage.SYSTEM,
        risk_level=RiskLevel.HIGH,
        disposition=RiskDisposition.APPROVAL_REQUIRED,
    )
    payload[field] = values

    with pytest.raises(ValueError, match=field):
        RiskAssessment.model_validate(payload)


def test_guardian_presence_is_exactly_bound_to_high_system_risk() -> None:
    effect = CanonicalEffect.model_validate(_effect_payload())

    non_high = _guardian_payload(effect.effect_sha256, system_level=RiskLevel.MEDIUM)
    with pytest.raises(ValueError, match="guardian assessment"):
        GuardianDecision.model_validate(non_high)

    high_missing = _guardian_payload(effect.effect_sha256, include_guardian=False)
    with pytest.raises(ValueError, match="guardian assessment"):
        GuardianDecision.model_validate(high_missing)


def test_guardian_rejects_an_effect_mismatch() -> None:
    effect = CanonicalEffect.model_validate(_effect_payload())
    mismatched = _assessment_payload(
        OTHER_SHA,
        stage=RiskStage.GUARDIAN,
        risk_level=RiskLevel.HIGH,
        disposition=RiskDisposition.APPROVAL_REQUIRED,
    )
    payload = _guardian_payload(effect.effect_sha256, guardian_assessment=mismatched)

    with pytest.raises(ValueError, match="effect"):
        GuardianDecision.model_validate(payload)


def test_safety_decision_rejects_changed_approval_arguments_even_with_a_new_digest() -> None:
    effect = _effect_payload()
    guardian = _guardian_payload(effect["effect_sha256"])
    payload = _safety_payload(effect, guardian)
    payload["approval_disclosure"]["safe_arguments"] = {"path": "different.md"}
    payload["decision_sha256"] = canonical_sha256(
        "agentteams-safety-decision",
        {key: value for key, value in payload.items() if key != "decision_sha256"},
    )

    with pytest.raises(ValueError, match="arguments"):
        SafetyDecision.model_validate(payload)


def test_safety_decision_rejects_a_changed_digest() -> None:
    effect = _effect_payload()
    guardian = _guardian_payload(effect["effect_sha256"])
    payload = _safety_payload(effect, guardian)
    payload["decision_sha256"] = OTHER_SHA

    with pytest.raises(ValueError, match="digest"):
        SafetyDecision.model_validate(payload)


def test_approval_disclosure_forbids_bearer_material() -> None:
    effect = _effect_payload()
    disclosure = _disclosure_payload(effect)
    disclosure["bearer_token"] = "secret"

    with pytest.raises(ValueError, match="bearer_token"):
        ApprovalDisclosure.model_validate(disclosure)


def test_attention_packet_rejects_token_overflow() -> None:
    payload = _attention_packet_payload(estimated_tokens=801)

    with pytest.raises(ValueError, match="token"):
        AttentionPacket.model_validate(payload)


@pytest.mark.parametrize(
    "fact",
    [
        _fact_ref_payload(project_id="project-other"),
        _fact_ref_payload(evidence_watermark=11),
    ],
)
def test_attention_packet_rejects_cross_project_or_stale_facts(fact: Dict[str, Any]) -> None:
    payload = _attention_packet_payload(eligible_fact_refs=(fact,))

    with pytest.raises(ValueError, match="project|watermark|stale"):
        AttentionPacket.model_validate(payload)


def test_normal_projection_rejects_a_grandchild_scope() -> None:
    payload = _projection_payload(
        visible_node_ids=("project-1", "task-1", "task-2", "subtask-1")
    )

    with pytest.raises(ValueError, match="current|direct child|depth"):
        UserStatusProjection.model_validate(payload)


def test_approval_projection_requires_present_and_exact_safety_material() -> None:
    missing = _projection_payload(mode=UserMessageMode.APPROVAL)
    with pytest.raises(ValueError, match="approval|Guardian|Safety|disclosure"):
        UserStatusProjection.model_validate(missing)

    effect = _effect_payload()
    guardian = _guardian_payload(effect["effect_sha256"])
    safety = _safety_payload(effect, guardian)
    stale = _projection_payload(
        mode=UserMessageMode.APPROVAL,
        guardian=guardian,
        safety=safety,
        disclosure=safety["approval_disclosure"],
    )
    stale["safety_decision_sha256"] = SHA
    stale["projection_sha256"] = canonical_sha256(
        "agentteams-user-status-projection",
        {key: value for key, value in stale.items() if key != "projection_sha256"},
    )
    with pytest.raises(ValueError, match="Safety|safety|digest"):
        UserStatusProjection.model_validate(stale)


@pytest.mark.parametrize(
    "explained_terms",
    [
        (("watermark", "Sequence."), ("checkpoint", "Snapshot.")),
        (("checkpoint", "Snapshot."), ("checkpoint", "Repeated.")),
    ],
)
def test_projection_rejects_unsorted_or_duplicate_glossary_terms(
    explained_terms: tuple[tuple[str, str], ...]
) -> None:
    payload = _projection_payload()
    payload["explained_terms"] = explained_terms

    with pytest.raises(ValueError, match="explained_terms"):
        UserStatusProjection.model_validate(payload)


def test_security_projection_accepts_an_explicit_scope_override() -> None:
    effect = _effect_payload()
    guardian = _guardian_payload(effect["effect_sha256"])
    safety = _safety_payload(effect, guardian)
    payload = _projection_payload(
        mode=UserMessageMode.SECURITY,
        visible_node_ids=("project-1", "task-1", "task-2", "subtask-1"),
        override_node_ids=("subtask-1",),
        guardian=guardian,
        safety=safety,
    )

    projection = UserStatusProjection.model_validate(payload)

    assert projection.override_node_ids == ("subtask-1",)
    assert projection.hierarchy.current_node_id == "project-1"
    assert WorkHierarchy.model_validate(_hierarchy_payload()).direct_child_ids == (
        "task-1",
        "task-2",
    )
    assert WorkNode.model_validate(_hierarchy_payload()["nodes"][0]).level is WorkLevel.PROJECT


def test_attention_fact_ref_forbids_secret_or_capability_material() -> None:
    payload = _fact_ref_payload()
    payload["capability_token"] = "bearer secret"

    with pytest.raises(ValueError, match="capability_token"):
        AttentionFactRef.model_validate(payload)
