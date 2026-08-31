from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict

import pytest

from apps.agentteams_bridge.extensions.contracts import (
    CampaignBinding,
    CanonicalEffect,
    EnforcementMode,
    RiskDisposition,
    RiskLevel,
    SafetyVerdict,
)
from apps.agentteams_bridge.extensions.guardian import EgoGuardian, build_guardian_decision
from apps.agentteams_bridge.extensions.safety import (
    ApprovalReceipt,
    SystemRiskClassifier,
    build_approval_receipt,
    evaluate_effect_safety,
    validate_approval_receipt,
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
        "final_arguments": {"path": "report.md", "text": "accepted evidence"},
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


def _effect(**overrides: Any) -> CanonicalEffect:
    return CanonicalEffect.model_validate(_effect_payload(**overrides))


def _campaign() -> CampaignBinding:
    return CampaignBinding(
        schema_version="agentteams-campaign-envelope/v1",
        campaign_id="campaign-1",
        configuration_id=MeasuredConfigurationId.D,
        execution_phase_owner=ExecutionPhaseOwner.D,
        problem_id="problem-1",
        turn=1,
        generation=1,
        manifest_sha256=SHA,
        post_selection_extension_sha256=None,
        policy_sha256=OTHER_SHA,
        requirement_ledger_sha256=SHA,
        workspace_checkpoint_sha256=SHA,
        memory_watermark=0,
    )


@pytest.mark.parametrize(
    ("operation", "expected_level"),
    [
        ("workspace.read", RiskLevel.LOW),
        ("workspace.write", RiskLevel.MEDIUM),
    ],
)
def test_low_and_medium_effects_are_allowed_without_a_guardian(
    operation: str, expected_level: RiskLevel
) -> None:
    effect = _effect(operation=operation)

    decision = evaluate_effect_safety(
        effect,
        sequence=7,
        approval_expires_at_sequence=11,
    )

    assert decision.guardian_decision.system_assessment.risk_level is expected_level
    assert decision.guardian_decision.system_assessment.effect_sha256 == effect.effect_sha256
    assert decision.guardian_decision.guardian_assessment is None
    assert decision.verdict is SafetyVerdict.ALLOW
    assert decision.approval_pending is False
    assert decision.approval_disclosure is None


def test_unknown_operation_fails_closed() -> None:
    effect = _effect(operation="workspace.unreviewed_magic")

    decision = evaluate_effect_safety(
        effect,
        sequence=7,
        approval_expires_at_sequence=11,
    )

    assessment = decision.guardian_decision.system_assessment
    assert assessment.risk_level is RiskLevel.HIGH
    assert assessment.disposition is RiskDisposition.DENY
    assert "UNKNOWN_OPERATION" in assessment.reason_codes
    assert "constraint.known-operation" in assessment.mandatory_constraint_ids
    assert decision.verdict is SafetyVerdict.DENY


def test_benign_token_budget_is_not_misclassified_as_secret_exfiltration() -> None:
    effect = _effect(
        operation="workspace.network.send",
        final_arguments={"destination": "remote-service", "token_budget": 200},
        target="workspace/network-request",
    )

    decision = evaluate_effect_safety(
        effect,
        sequence=7,
        approval_expires_at_sequence=11,
    )

    assert decision.verdict is SafetyVerdict.APPROVAL_REQUIRED
    assert "SECRET_EXFILTRATION" not in (decision.guardian_decision.system_assessment.reason_codes)


def test_network_url_is_not_misclassified_as_a_filesystem_path_escape() -> None:
    effect = _effect(
        operation="workspace.network.send",
        final_arguments={"destination": "https://example.invalid/upload"},
        target="https://example.invalid/upload",
    )

    decision = evaluate_effect_safety(
        effect,
        sequence=7,
        approval_expires_at_sequence=11,
    )

    assert decision.verdict is SafetyVerdict.APPROVAL_REQUIRED
    assert "PATH_ESCAPE" not in decision.guardian_decision.system_assessment.reason_codes


@pytest.mark.parametrize(
    ("overrides", "reason", "constraint"),
    [
        (
            {"final_arguments": {"path": "../host/secret"}},
            "PATH_ESCAPE",
            "constraint.workspace-boundary",
        ),
        (
            {"affected_scope": ("project:project-2", "task:task-1")},
            "CROSS_PROJECT_TARGET",
            "constraint.project-boundary",
        ),
        (
            {
                "operation": "workspace.network.send",
                "final_arguments": {
                    "data_classification": "SECRET",
                    "destination": "https://example.invalid/upload",
                },
            },
            "SECRET_EXFILTRATION",
            "constraint.no-secret-exfiltration",
        ),
        (
            {"operation": "workspace.delete", "reversibility": "IRREVERSIBLE"},
            "IRREVERSIBLE_DESTRUCTION",
            "constraint.recoverable-effects",
        ),
        (
            {"target": "workspace/evidence/decision.json"},
            "EVIDENCE_TAMPERING",
            "constraint.evidence-immutability",
        ),
        (
            {"operation": "agentteams.shell"},
            "WORKSPACE_GATEWAY_BYPASS",
            "constraint.workspace-gateway-only",
        ),
    ],
)
def test_mandatory_rules_are_high_and_deny_without_approval(
    overrides: Dict[str, Any], reason: str, constraint: str
) -> None:
    effect = _effect(**overrides)

    decision = evaluate_effect_safety(
        effect,
        sequence=7,
        approval_expires_at_sequence=11,
    )

    system = decision.guardian_decision.system_assessment
    guardian = decision.guardian_decision.guardian_assessment
    assert system.risk_level is RiskLevel.HIGH
    assert system.disposition is RiskDisposition.DENY
    assert reason in system.reason_codes
    assert constraint in system.mandatory_constraint_ids
    assert guardian is not None
    assert guardian.risk_level is RiskLevel.HIGH
    assert guardian.disposition is RiskDisposition.DENY
    assert set(system.mandatory_constraint_ids).issubset(guardian.mandatory_constraint_ids)
    assert decision.verdict is SafetyVerdict.DENY
    assert decision.approval_pending is False
    assert decision.approval_disclosure is None


def test_double_high_approval_binds_the_exact_final_effect() -> None:
    effect = _effect(operation="workspace.delete")

    decision = evaluate_effect_safety(
        effect,
        sequence=7,
        approval_expires_at_sequence=11,
        enforcement_mode=EnforcementMode.ENFORCING,
    )

    guardian = decision.guardian_decision.guardian_assessment
    disclosure = decision.approval_disclosure
    assert guardian is not None
    assert guardian.risk_level is RiskLevel.HIGH
    assert guardian.disposition is RiskDisposition.APPROVAL_REQUIRED
    assert decision.verdict is SafetyVerdict.APPROVAL_REQUIRED
    assert decision.approval_pending is True
    assert disclosure is not None
    assert disclosure.effect_sha256 == effect.effect_sha256
    assert disclosure.safe_arguments == effect.final_arguments
    assert disclosure.target == effect.target
    assert disclosure.affected_scope == effect.affected_scope
    assert disclosure.recovery_plan == effect.recovery_plan
    assert disclosure.expires_at_sequence == 11
    assert disclosure.allowed_responses == ("APPROVE", "DENY")


def _approval_decision() -> tuple[CampaignBinding, Any]:
    campaign = _campaign()
    decision = evaluate_effect_safety(
        _effect(operation="workspace.delete"),
        sequence=7,
        approval_expires_at_sequence=11,
    )
    return campaign, decision


def _redigest_receipt(payload: Dict[str, Any]) -> None:
    core = {key: value for key, value in payload.items() if key != "receipt_sha256"}
    payload["receipt_sha256"] = canonical_sha256("agentteams-approval-receipt", core)


def test_exact_approval_receipt_validates_without_secret_material() -> None:
    campaign, decision = _approval_decision()
    receipt = build_approval_receipt(
        campaign=campaign,
        safety_decision=decision,
        approver="judge@example.invalid",
        nonce="approval-nonce-1",
        approved_at_sequence=9,
    )

    validated = validate_approval_receipt(
        receipt,
        campaign=campaign,
        safety_decision=decision,
        control_ledger_receipt_sha256=receipt.receipt_sha256,
        expected_approver="judge@example.invalid",
        expected_nonce="approval-nonce-1",
        expected_approved_at_sequence=9,
        current_sequence=9,
    )

    assert validated == receipt
    assert set(receipt.model_dump()) == {
        "schema_version",
        "campaign_id",
        "campaign_sha256",
        "effect_sha256",
        "final_arguments",
        "target",
        "affected_scope",
        "expires_at_sequence",
        "approved_at_sequence",
        "approver",
        "nonce",
        "receipt_sha256",
    }
    assert not any("token" in key or "secret" in key for key in receipt.model_dump())


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("campaign_id", "campaign-other"),
        ("campaign_sha256", "c" * 64),
        ("effect_sha256", "c" * 64),
        ("final_arguments", {"path": "different.md"}),
        ("target", "workspace/different.md"),
        ("affected_scope", ("project:project-1", "task:task-other")),
        ("expires_at_sequence", 12),
        ("approved_at_sequence", 8),
        ("approver", "other@example.invalid"),
        ("nonce", "approval-nonce-other"),
    ],
)
def test_changed_approval_receipt_field_is_rejected_even_if_redigested(
    field: str, replacement: Any
) -> None:
    campaign, decision = _approval_decision()
    receipt = build_approval_receipt(
        campaign=campaign,
        safety_decision=decision,
        approver="judge@example.invalid",
        nonce="approval-nonce-1",
        approved_at_sequence=9,
    )
    changed = deepcopy(receipt.model_dump(mode="python"))
    changed[field] = replacement
    _redigest_receipt(changed)

    with pytest.raises(ValueError):
        validate_approval_receipt(
            changed,
            campaign=campaign,
            safety_decision=decision,
            control_ledger_receipt_sha256=receipt.receipt_sha256,
            expected_approver="judge@example.invalid",
            expected_nonce="approval-nonce-1",
            expected_approved_at_sequence=9,
            current_sequence=9,
        )


def test_changed_receipt_digest_stale_receipt_and_bearer_material_are_rejected() -> None:
    campaign, decision = _approval_decision()
    receipt = build_approval_receipt(
        campaign=campaign,
        safety_decision=decision,
        approver="judge@example.invalid",
        nonce="approval-nonce-1",
        approved_at_sequence=9,
    )
    changed_digest = receipt.model_dump(mode="python")
    changed_digest["receipt_sha256"] = "c" * 64
    with pytest.raises(ValueError, match="digest"):
        ApprovalReceipt.model_validate(changed_digest)

    with pytest.raises(ValueError, match="expired"):
        validate_approval_receipt(
            receipt,
            campaign=campaign,
            safety_decision=decision,
            control_ledger_receipt_sha256=receipt.receipt_sha256,
            expected_approver="judge@example.invalid",
            expected_nonce="approval-nonce-1",
            expected_approved_at_sequence=9,
            current_sequence=12,
        )

    bearer = receipt.model_dump(mode="python")
    bearer["approval_token"] = "must-not-be-recorded"
    with pytest.raises(ValueError, match="approval_token"):
        ApprovalReceipt.model_validate(bearer)


def test_bare_self_hashed_receipt_requires_a_matching_control_ledger_record() -> None:
    campaign, decision = _approval_decision()
    receipt = build_approval_receipt(
        campaign=campaign,
        safety_decision=decision,
        approver="judge@example.invalid",
        nonce="approval-nonce-1",
        approved_at_sequence=9,
    )

    with pytest.raises(ValueError, match="Control ledger"):
        validate_approval_receipt(
            receipt,
            campaign=campaign,
            safety_decision=decision,
            control_ledger_receipt_sha256="c" * 64,
            expected_approver="judge@example.invalid",
            expected_nonce="approval-nonce-1",
            expected_approved_at_sequence=9,
            current_sequence=9,
        )


def test_system_rule_version_and_digest_are_fixed_and_replayable() -> None:
    effect = _effect(operation="workspace.write")

    first = SystemRiskClassifier.assess(effect, sequence=7)
    second = SystemRiskClassifier.assess(effect, sequence=7)

    assert first == second
    assert first.rule_version == SystemRiskClassifier.RULE_VERSION
    assert first.rule_sha256 == SystemRiskClassifier.RULE_SHA256
    assert first.reason_codes == tuple(sorted(set(first.reason_codes)))
    assert first.mandatory_constraint_ids == tuple(sorted(set(first.mandatory_constraint_ids)))


def test_extension_package_exports_the_public_safety_api() -> None:
    from apps.agentteams_bridge import extensions

    assert extensions.ApprovalReceipt is ApprovalReceipt
    assert extensions.EgoGuardian is EgoGuardian
    assert extensions.SystemRiskClassifier is SystemRiskClassifier
    assert extensions.build_guardian_decision is build_guardian_decision
    assert extensions.build_approval_receipt is build_approval_receipt
    assert extensions.evaluate_effect_safety is evaluate_effect_safety
    assert extensions.validate_approval_receipt is validate_approval_receipt
