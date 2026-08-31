from __future__ import annotations

from typing import Any, Dict, Literal, Mapping, Tuple, Union

from pydantic import Field, JsonValue, field_validator, model_validator

from benchmarks.secure_memory.canonical import (
    canonical_bytes,
    canonical_sha256,
    validate_sha256_digest,
)
from benchmarks.secure_memory.models import Digest, StrictModel

from .contracts import (
    ApprovalDisclosure,
    CampaignBinding,
    CanonicalEffect,
    EnforcementMode,
    RiskAssessment,
    RiskDisposition,
    RiskLevel,
    RiskStage,
    SafetyDecision,
    SafetyVerdict,
)
from .guardian import (
    KNOWN_OPERATIONS,
    MANDATORY_CONSTRAINT_REGISTRY,
    build_guardian_decision,
    mandatory_rule_matches,
    operation_risk_level,
)


class SystemRiskClassifier:
    """Pure deterministic first-stage classifier over the final canonical effect."""

    RULE_VERSION = "agentteams-system-risk-rules/2026-08-31"
    _RULE_TABLE = {
        "known_operations": tuple(sorted(KNOWN_OPERATIONS)),
        "mandatory_constraints": tuple(sorted(MANDATORY_CONSTRAINT_REGISTRY.items())),
        "unknown_operation": "HIGH/DENY",
        "known_high": "HIGH/APPROVAL_REQUIRED",
        "known_low_medium": "ALLOW",
    }
    RULE_SHA256 = canonical_sha256("agentteams-system-risk-rules", _RULE_TABLE)

    @classmethod
    def assess(cls, effect: CanonicalEffect, *, sequence: int) -> RiskAssessment:
        matches = mandatory_rule_matches(effect)
        reasons = {reason for reason, _constraint in matches}
        constraints = {constraint for _reason, constraint in matches}
        known = effect.operation in KNOWN_OPERATIONS
        if not known:
            reasons.add("UNKNOWN_OPERATION")
            constraints.add("constraint.known-operation")

        risk_level = operation_risk_level(effect.operation)
        if constraints:
            risk_level = RiskLevel.HIGH
            disposition = RiskDisposition.DENY
        elif risk_level is RiskLevel.HIGH:
            reasons.add("SYSTEM_HIGH_IMPACT_EFFECT")
            disposition = RiskDisposition.APPROVAL_REQUIRED
        else:
            reasons.add(
                "READ_ONLY_WORKSPACE_EFFECT"
                if risk_level is RiskLevel.LOW
                else "REVERSIBLE_WORKSPACE_MUTATION"
            )
            disposition = RiskDisposition.ALLOW

        return RiskAssessment(
            schema_version="agentteams-risk-assessment/v1",
            effect_sha256=effect.effect_sha256,
            stage=RiskStage.SYSTEM,
            risk_level=risk_level,
            disposition=disposition,
            reason_codes=tuple(sorted(reasons)),
            mandatory_constraint_ids=tuple(sorted(constraints)),
            rule_version=cls.RULE_VERSION,
            rule_sha256=cls.RULE_SHA256,
            sequence=sequence,
        )


def evaluate_effect_safety(
    effect: CanonicalEffect,
    *,
    sequence: int,
    approval_expires_at_sequence: int,
    enforcement_mode: EnforcementMode = EnforcementMode.ENFORCING,
) -> SafetyDecision:
    """Run system then Guardian classification and produce one exact decision."""

    system = SystemRiskClassifier.assess(effect, sequence=sequence)
    guardian_decision = build_guardian_decision(
        effect,
        system,
        enforcement_mode=enforcement_mode,
        guardian_sequence=sequence + 1,
    )
    verdict = SafetyVerdict(guardian_decision.disposition.value)
    disclosure = None
    approval_pending = verdict is SafetyVerdict.APPROVAL_REQUIRED
    if approval_pending:
        if approval_expires_at_sequence <= sequence:
            raise ValueError("approval expiry must be after the system assessment sequence")
        assessments = [system]
        if guardian_decision.guardian_assessment is not None:
            assessments.append(guardian_decision.guardian_assessment)
        reason_codes = tuple(
            sorted({reason for assessment in assessments for reason in assessment.reason_codes})
        )
        disclosure = ApprovalDisclosure(
            schema_version="agentteams-approval-disclosure/v1",
            effect_sha256=effect.effect_sha256,
            safe_arguments=effect.final_arguments,
            target=effect.target,
            affected_scope=effect.affected_scope,
            reason_codes=reason_codes,
            recovery_plan=effect.recovery_plan,
            expires_at_sequence=approval_expires_at_sequence,
            allowed_responses=("APPROVE", "DENY"),
        )

    values: Dict[str, Any] = {
        "schema_version": "agentteams-safety-decision/v1",
        "effect": effect,
        "guardian_decision": guardian_decision,
        "verdict": verdict,
        "approval_pending": approval_pending,
        "approval_disclosure": disclosure,
    }
    values["decision_sha256"] = canonical_sha256("agentteams-safety-decision", values)
    return SafetyDecision.model_validate(values)


class ApprovalReceipt(StrictModel):
    """Secret-free Control receipt bound to one exact pending effect."""

    schema_version: Literal["agentteams-approval-receipt/v1"]
    campaign_id: str = Field(min_length=1)
    campaign_sha256: Digest
    effect_sha256: Digest
    final_arguments: Dict[str, JsonValue]
    target: str = Field(min_length=1)
    affected_scope: Tuple[str, ...]
    expires_at_sequence: int = Field(ge=0)
    approved_at_sequence: int = Field(ge=0)
    approver: str = Field(min_length=1)
    nonce: str = Field(min_length=1)
    receipt_sha256: Digest

    @field_validator("affected_scope")
    @classmethod
    def validate_affected_scope(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        if any(not item for item in value):
            raise ValueError("affected_scope values must be non-empty")
        if value != tuple(sorted(value)) or len(value) != len(set(value)):
            raise ValueError("affected_scope must be sorted and duplicate-free")
        return value

    @model_validator(mode="after")
    def validate_receipt_digest(self) -> "ApprovalReceipt":
        core = self.model_dump(mode="python", exclude={"receipt_sha256"})
        expected = canonical_sha256("agentteams-approval-receipt", core)
        if self.receipt_sha256 != expected:
            raise ValueError("approval receipt digest does not match")
        if self.approved_at_sequence > self.expires_at_sequence:
            raise ValueError("approval receipt was issued after expiry")
        return self


def campaign_binding_sha256(campaign: CampaignBinding) -> str:
    return canonical_sha256("agentteams-campaign-binding", campaign)


def _approval_context(
    campaign: CampaignBinding,
    safety_decision: SafetyDecision,
) -> Tuple[CanonicalEffect, ApprovalDisclosure]:
    if safety_decision.verdict is not SafetyVerdict.APPROVAL_REQUIRED:
        raise ValueError("approval receipt requires an APPROVAL_REQUIRED decision")
    disclosure = safety_decision.approval_disclosure
    if not safety_decision.approval_pending or disclosure is None:
        raise ValueError("approval receipt requires an exact pending disclosure")
    effect = safety_decision.effect
    if effect.policy_sha256 != campaign.policy_sha256:
        raise ValueError("approval effect policy does not match the campaign")
    if effect.workspace_checkpoint_sha256 != campaign.workspace_checkpoint_sha256:
        raise ValueError("approval effect checkpoint does not match the campaign")
    return effect, disclosure


def build_approval_receipt(
    *,
    campaign: CampaignBinding,
    safety_decision: SafetyDecision,
    approver: str,
    nonce: str,
    approved_at_sequence: int,
) -> ApprovalReceipt:
    """Build a typed receipt for Control admission, without retaining a bearer.

    This pure constructor does not confer authority. A trusted Control boundary
    must authenticate/sign and admit the receipt before a consumer validates it.
    """

    effect, disclosure = _approval_context(campaign, safety_decision)
    values: Dict[str, Any] = {
        "schema_version": "agentteams-approval-receipt/v1",
        "campaign_id": campaign.campaign_id,
        "campaign_sha256": campaign_binding_sha256(campaign),
        "effect_sha256": effect.effect_sha256,
        "final_arguments": effect.final_arguments,
        "target": effect.target,
        "affected_scope": effect.affected_scope,
        "expires_at_sequence": disclosure.expires_at_sequence,
        "approved_at_sequence": approved_at_sequence,
        "approver": approver,
        "nonce": nonce,
    }
    values["receipt_sha256"] = canonical_sha256("agentteams-approval-receipt", values)
    return ApprovalReceipt.model_validate(values)


def validate_approval_receipt(
    receipt: Union[ApprovalReceipt, Mapping[str, Any]],
    *,
    campaign: CampaignBinding,
    safety_decision: SafetyDecision,
    control_ledger_receipt_sha256: str,
    expected_approver: str,
    expected_nonce: str,
    expected_approved_at_sequence: int,
    current_sequence: int,
) -> ApprovalReceipt:
    """Validate exact bindings against an already-verified Control ledger digest.

    ``control_ledger_receipt_sha256`` must come from the trusted Control ledger
    or a signature-verification boundary. The receipt's self-hash is integrity
    metadata and is never accepted as its own authority.
    """

    validated = ApprovalReceipt.model_validate(receipt)
    validate_sha256_digest(control_ledger_receipt_sha256)
    effect, disclosure = _approval_context(campaign, safety_decision)
    problems = []
    if validated.receipt_sha256 != control_ledger_receipt_sha256:
        problems.append("Control ledger authority digest")
    if validated.campaign_id != campaign.campaign_id:
        problems.append("campaign ID")
    if validated.campaign_sha256 != campaign_binding_sha256(campaign):
        problems.append("campaign digest")
    if validated.effect_sha256 != effect.effect_sha256:
        problems.append("effect digest")
    if canonical_bytes(validated.final_arguments) != canonical_bytes(effect.final_arguments):
        problems.append("final arguments")
    if validated.target != effect.target:
        problems.append("target")
    if validated.affected_scope != effect.affected_scope:
        problems.append("affected scope")
    if validated.expires_at_sequence != disclosure.expires_at_sequence:
        problems.append("expiry")
    if validated.approver != expected_approver:
        problems.append("approver")
    if validated.nonce != expected_nonce:
        problems.append("nonce")
    if validated.approved_at_sequence != expected_approved_at_sequence:
        problems.append("approval sequence")
    if current_sequence > validated.expires_at_sequence:
        problems.append("expired approval")
    if current_sequence < validated.approved_at_sequence:
        problems.append("future approval")
    if problems:
        raise ValueError("approval receipt mismatch: " + ", ".join(problems))
    return validated


__all__ = [
    "ApprovalReceipt",
    "SystemRiskClassifier",
    "build_approval_receipt",
    "campaign_binding_sha256",
    "evaluate_effect_safety",
    "validate_approval_receipt",
]
