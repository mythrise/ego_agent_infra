from __future__ import annotations

import math
from enum import Enum
from typing import Any, Dict, Literal, Optional, Tuple

from pydantic import Field, JsonValue, field_validator, model_validator

from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import (
    Digest,
    ExecutionPhaseOwner,
    MeasuredConfigurationId,
    StrictModel,
)


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class RiskStage(str, Enum):
    SYSTEM = "SYSTEM"
    GUARDIAN = "GUARDIAN"


class EnforcementMode(str, Enum):
    COUNTERFACTUAL = "COUNTERFACTUAL"
    ENFORCING = "ENFORCING"


class SafetyVerdict(str, Enum):
    ALLOW = "ALLOW"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DENY = "DENY"


class RiskDisposition(str, Enum):
    ALLOW = "ALLOW"
    APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
    DENY = "DENY"


def _validate_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{path} must contain only finite JSON numbers")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} JSON object keys must be strings")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains unsupported JSON value {type(value).__name__}")


def _validate_sorted_unique(values: Tuple[str, ...], field_name: str) -> Tuple[str, ...]:
    if any(not value for value in values):
        raise ValueError(f"{field_name} values must be non-empty")
    if tuple(sorted(values)) != values or len(set(values)) != len(values):
        raise ValueError(f"{field_name} must be sorted and duplicate-free")
    return values


def _expected_model_digest(model: StrictModel, domain: str, digest_field: str) -> str:
    core = model.model_dump(mode="python", exclude={digest_field})
    return canonical_sha256(domain, core)


class CampaignBinding(StrictModel):
    schema_version: Literal["agentteams-campaign-envelope/v1"]
    campaign_id: str = Field(min_length=1)
    configuration_id: Optional[MeasuredConfigurationId]
    execution_phase_owner: ExecutionPhaseOwner
    problem_id: str = Field(min_length=1)
    turn: int = Field(ge=1, le=5)
    generation: int = Field(ge=1)
    manifest_sha256: Digest
    post_selection_extension_sha256: Optional[Digest]
    policy_sha256: Digest
    requirement_ledger_sha256: Digest
    workspace_checkpoint_sha256: Digest
    memory_watermark: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_owner_binding(self) -> "CampaignBinding":
        measured_owners = {
            ExecutionPhaseOwner.A,
            ExecutionPhaseOwner.B,
            ExecutionPhaseOwner.C,
            ExecutionPhaseOwner.D,
            ExecutionPhaseOwner.E,
            ExecutionPhaseOwner.F,
        }
        if self.execution_phase_owner in measured_owners:
            if (
                self.configuration_id is None
                or self.configuration_id.value != self.execution_phase_owner.value
            ):
                raise ValueError("execution phase owner must match configuration_id")
        elif self.execution_phase_owner in {
            ExecutionPhaseOwner.QUALIFICATION,
            ExecutionPhaseOwner.OPTIMIZER,
        }:
            if self.configuration_id is not None:
                raise ValueError("qualification/optimizer owners cannot bind a configuration")
        return self


class CanonicalEffect(StrictModel):
    schema_version: Literal["agentteams-canonical-effect/v1"]
    effect_id: str = Field(min_length=1)
    operation: str = Field(min_length=1)
    final_arguments: Dict[str, JsonValue]
    target: str = Field(min_length=1)
    affected_scope: Tuple[str, ...]
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    workspace_checkpoint_sha256: Digest
    policy_sha256: Digest
    reversibility: str = Field(min_length=1)
    recovery_plan: str = Field(min_length=1)
    effect_sha256: Digest

    @field_validator("final_arguments", mode="before")
    @classmethod
    def validate_final_arguments(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("final_arguments must be a JSON object")
        _validate_json_value(value, "final_arguments")
        return value

    @field_validator("affected_scope")
    @classmethod
    def validate_affected_scope(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        return _validate_sorted_unique(value, "affected_scope")

    @model_validator(mode="after")
    def validate_effect_digest(self) -> "CanonicalEffect":
        expected = _expected_model_digest(self, "agentteams-canonical-effect", "effect_sha256")
        if self.effect_sha256 != expected:
            raise ValueError("effect digest does not match the canonical effect")
        return self


class RiskAssessment(StrictModel):
    schema_version: Literal["agentteams-risk-assessment/v1"]
    effect_sha256: Digest
    stage: RiskStage
    risk_level: RiskLevel
    disposition: RiskDisposition
    reason_codes: Tuple[str, ...]
    mandatory_constraint_ids: Tuple[str, ...]
    rule_version: str = Field(min_length=1)
    rule_sha256: Digest
    sequence: int = Field(ge=0)

    @field_validator("reason_codes", "mandatory_constraint_ids")
    @classmethod
    def validate_sorted_ids(cls, value: Tuple[str, ...], info: Any) -> Tuple[str, ...]:
        return _validate_sorted_unique(value, info.field_name)


_RISK_RANK = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
}
_DISPOSITION_RANK = {
    RiskDisposition.ALLOW: 0,
    RiskDisposition.APPROVAL_REQUIRED: 1,
    RiskDisposition.DENY: 2,
}


class GuardianDecision(StrictModel):
    schema_version: Literal["agentteams-guardian-decision/v1"]
    effect_sha256: Digest
    system_assessment: RiskAssessment
    guardian_assessment: Optional[RiskAssessment]
    enforcement_mode: EnforcementMode
    disposition: RiskDisposition
    decision_sha256: Digest

    @model_validator(mode="after")
    def validate_guardian_decision(self) -> "GuardianDecision":
        system = self.system_assessment
        guardian = self.guardian_assessment
        if system.stage is not RiskStage.SYSTEM:
            raise ValueError("system assessment must use the SYSTEM stage")
        if system.effect_sha256 != self.effect_sha256:
            raise ValueError("system assessment effect does not match the guardian decision")

        requires_guardian = system.risk_level is RiskLevel.HIGH
        if requires_guardian != (guardian is not None):
            raise ValueError("guardian assessment must be present exactly for HIGH system risk")

        effective = system
        if guardian is not None:
            if guardian.stage is not RiskStage.GUARDIAN:
                raise ValueError("guardian assessment must use the GUARDIAN stage")
            if guardian.effect_sha256 != self.effect_sha256:
                raise ValueError("guardian assessment effect does not match the guardian decision")
            if not set(system.mandatory_constraint_ids).issubset(
                guardian.mandatory_constraint_ids
            ):
                raise ValueError("guardian assessment cannot omit mandatory constraints")
            if _RISK_RANK[guardian.risk_level] < _RISK_RANK[system.risk_level]:
                raise ValueError("guardian assessment cannot downgrade risk")
            if _DISPOSITION_RANK[guardian.disposition] < _DISPOSITION_RANK[system.disposition]:
                raise ValueError("guardian assessment cannot downgrade the disposition")
            effective = guardian

        if self.disposition is not effective.disposition:
            raise ValueError("guardian decision disposition must match the effective assessment")
        expected = _expected_model_digest(
            self,
            "agentteams-guardian-decision",
            "decision_sha256",
        )
        if self.decision_sha256 != expected:
            raise ValueError("guardian decision digest does not match its canonical payload")
        return self


class ApprovalDisclosure(StrictModel):
    schema_version: Literal["agentteams-approval-disclosure/v1"]
    effect_sha256: Digest
    safe_arguments: Dict[str, JsonValue]
    target: str = Field(min_length=1)
    affected_scope: Tuple[str, ...]
    reason_codes: Tuple[str, ...]
    recovery_plan: str = Field(min_length=1)
    expires_at_sequence: int = Field(ge=0)
    allowed_responses: Tuple[Literal["APPROVE"], Literal["DENY"]]

    @field_validator("safe_arguments", mode="before")
    @classmethod
    def validate_safe_arguments(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("safe_arguments must be a JSON object")
        _validate_json_value(value, "safe_arguments")
        return value

    @field_validator("affected_scope", "reason_codes")
    @classmethod
    def validate_sorted_values(cls, value: Tuple[str, ...], info: Any) -> Tuple[str, ...]:
        return _validate_sorted_unique(value, info.field_name)


class SafetyDecision(StrictModel):
    schema_version: Literal["agentteams-safety-decision/v1"]
    effect: CanonicalEffect
    guardian_decision: GuardianDecision
    verdict: SafetyVerdict
    approval_pending: bool
    approval_disclosure: Optional[ApprovalDisclosure]
    decision_sha256: Digest

    @model_validator(mode="after")
    def validate_safety_decision(self) -> "SafetyDecision":
        guardian = self.guardian_decision
        if guardian.effect_sha256 != self.effect.effect_sha256:
            raise ValueError("guardian decision effect does not match the safety effect")
        if self.verdict.value != guardian.disposition.value:
            raise ValueError("safety verdict must match the guardian disposition")

        approval_required = self.verdict is SafetyVerdict.APPROVAL_REQUIRED
        if approval_required:
            if not self.approval_pending or self.approval_disclosure is None:
                raise ValueError(
                    "APPROVAL_REQUIRED decisions require pending approval and a disclosure"
                )
            self._validate_disclosure(self.approval_disclosure)
        elif self.approval_pending or self.approval_disclosure is not None:
            raise ValueError("non-approval decisions forbid pending approval and disclosures")

        expected = _expected_model_digest(
            self,
            "agentteams-safety-decision",
            "decision_sha256",
        )
        if self.decision_sha256 != expected:
            raise ValueError("safety decision digest does not match its canonical payload")
        return self

    def _validate_disclosure(self, disclosure: ApprovalDisclosure) -> None:
        effect = self.effect
        if disclosure.effect_sha256 != effect.effect_sha256:
            raise ValueError("approval disclosure effect does not match the safety effect")
        if disclosure.safe_arguments != effect.final_arguments:
            raise ValueError("approval disclosure arguments do not match the canonical effect")
        if disclosure.target != effect.target:
            raise ValueError("approval disclosure target does not match the canonical effect")
        if disclosure.affected_scope != effect.affected_scope:
            raise ValueError("approval disclosure scope does not match the canonical effect")
        if disclosure.recovery_plan != effect.recovery_plan:
            raise ValueError("approval disclosure recovery plan does not match the canonical effect")
        assessments = [self.guardian_decision.system_assessment]
        if self.guardian_decision.guardian_assessment is not None:
            assessments.append(self.guardian_decision.guardian_assessment)
        expected_reasons = tuple(
            sorted({reason for assessment in assessments for reason in assessment.reason_codes})
        )
        if disclosure.reason_codes != expected_reasons:
            raise ValueError("approval disclosure reasons do not match the risk assessments")


class AttentionFactRef(StrictModel):
    fact_sha256: Digest
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    component: str = Field(min_length=1)
    version: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    origin: str = Field(min_length=1)
    lifecycle: str = Field(min_length=1)
    relevance_score_basis_points: int = Field(ge=0, le=10_000)
    evidence_watermark: int = Field(ge=0)


class AttentionPacket(StrictModel):
    schema_version: Literal["agentteams-attention-packet/v1"]
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    turn: int = Field(ge=1, le=5)
    generation: int = Field(ge=1)
    requirement_ledger_sha256: Digest
    workspace_checkpoint_sha256: Digest
    policy_sha256: Digest
    memory_watermark: int = Field(ge=0)
    current_requirement_text: str = Field(min_length=1)
    unresolved_failure_ids: Tuple[str, ...]
    mandatory_policy_constraint_ids: Tuple[str, ...]
    eligible_fact_refs: Tuple[AttentionFactRef, ...]
    explicit_exclusions: Tuple[str, ...]
    token_budget: int = Field(gt=0)
    estimated_tokens: int = Field(ge=0)
    source_context_sha256: Digest
    packet_sha256: Digest

    @field_validator(
        "unresolved_failure_ids",
        "mandatory_policy_constraint_ids",
        "explicit_exclusions",
    )
    @classmethod
    def validate_sorted_identifiers(cls, value: Tuple[str, ...], info: Any) -> Tuple[str, ...]:
        return _validate_sorted_unique(value, info.field_name)

    @model_validator(mode="after")
    def validate_attention_packet(self) -> "AttentionPacket":
        if self.estimated_tokens > self.token_budget:
            raise ValueError("estimated token count exceeds token_budget")

        fact_ids = tuple(fact.fact_sha256 for fact in self.eligible_fact_refs)
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("eligible_fact_refs must contain unique fact digests")
        expected_fact_order = tuple(
            sorted(
                self.eligible_fact_refs,
                key=lambda fact: (-fact.relevance_score_basis_points, fact.fact_sha256),
            )
        )
        if self.eligible_fact_refs != expected_fact_order:
            raise ValueError(
                "eligible_fact_refs must be ordered by relevance then fact digest"
            )

        excluded = set(self.explicit_exclusions)
        if excluded.intersection(fact_ids):
            raise ValueError("eligible facts cannot also be explicitly excluded")
        for fact in self.eligible_fact_refs:
            if fact.tenant_id != self.tenant_id:
                raise ValueError("attention fact tenant does not match the packet tenant")
            if fact.project_id != self.project_id:
                raise ValueError("attention fact project does not match the packet project")
            if fact.evidence_watermark != self.memory_watermark:
                raise ValueError("attention fact has a stale evidence watermark")
            if fact.lifecycle != "VALIDATED":
                raise ValueError(
                    "eligible attention facts must have the VALIDATED lifecycle"
                )

        source_core = self.model_dump(
            mode="python",
            exclude={"source_context_sha256", "packet_sha256"},
        )
        expected_source = canonical_sha256("agentteams-attention-source-context", source_core)
        if self.source_context_sha256 != expected_source:
            raise ValueError("attention source/context digest does not match")
        expected_packet = _expected_model_digest(
            self,
            "agentteams-attention-packet",
            "packet_sha256",
        )
        if self.packet_sha256 != expected_packet:
            raise ValueError("attention packet digest does not match its canonical payload")
        return self


class WorkLevel(str, Enum):
    TENANT = "TENANT"
    PROJECT = "PROJECT"
    TASK = "TASK"
    SUBTASK = "SUBTASK"


_WORK_LEVEL_DEPTH = {
    WorkLevel.TENANT: 0,
    WorkLevel.PROJECT: 1,
    WorkLevel.TASK: 2,
    WorkLevel.SUBTASK: 3,
}


class WorkNode(StrictModel):
    node_id: str = Field(min_length=1)
    level: WorkLevel
    parent_id: Optional[str]
    child_ids: Tuple[str, ...]
    order: int = Field(ge=0)

    @field_validator("child_ids")
    @classmethod
    def validate_child_ids(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        if any(not child_id for child_id in value):
            raise ValueError("child_ids values must be non-empty")
        if len(set(value)) != len(value):
            raise ValueError("child_ids must be unique")
        return value


class WorkHierarchy(StrictModel):
    schema_version: Literal["agentteams-work-hierarchy/v1"]
    current_node_id: str = Field(min_length=1)
    direct_child_ids: Tuple[str, ...]
    nodes: Tuple[WorkNode, ...]

    @model_validator(mode="after")
    def validate_current_scope(self) -> "WorkHierarchy":
        node_by_id = {node.node_id: node for node in self.nodes}
        if len(node_by_id) != len(self.nodes):
            raise ValueError("work hierarchy node IDs must be unique")
        current = node_by_id.get(self.current_node_id)
        if current is None:
            raise ValueError("current work scope must be present in the hierarchy")
        if len(set(self.direct_child_ids)) != len(self.direct_child_ids):
            raise ValueError("direct_child_ids must be unique")
        if current.child_ids != self.direct_child_ids:
            raise ValueError("direct_child_ids must exactly match the current scope child order")

        children = [node for node in self.nodes if node.parent_id == current.node_id]
        ordered_children = tuple(node.node_id for node in sorted(children, key=lambda node: node.order))
        if ordered_children != self.direct_child_ids:
            raise ValueError("direct child nodes must exactly match their declared IDs and order")
        if tuple(sorted(node.order for node in children)) != tuple(range(len(children))):
            raise ValueError("direct child order must be contiguous from zero")
        expected_depth = _WORK_LEVEL_DEPTH[current.level] + 1
        if any(_WORK_LEVEL_DEPTH[child.level] != expected_depth for child in children):
            raise ValueError("work hierarchy children must be exactly one level below current")
        return self


class UserMessageMode(str, Enum):
    PROGRESS = "PROGRESS"
    DETAIL = "DETAIL"
    RISK = "RISK"
    APPROVAL = "APPROVAL"
    SECURITY = "SECURITY"


class UserStatusProjection(StrictModel):
    schema_version: Literal["agentteams-user-status-projection/v1"]
    tenant_id: str = Field(min_length=1)
    project_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    mode: UserMessageMode
    hierarchy: WorkHierarchy
    visible_node_ids: Tuple[str, ...]
    override_node_ids: Tuple[str, ...]
    status_text: str = Field(min_length=1)
    guardian_decision: Optional[GuardianDecision]
    guardian_decision_sha256: Optional[Digest]
    safety_decision: Optional[SafetyDecision]
    safety_decision_sha256: Optional[Digest]
    approval_disclosure: Optional[ApprovalDisclosure]
    explained_terms: Tuple[Tuple[str, str], ...]
    source_event_ids: Tuple[str, ...]
    projection_sha256: Digest

    @field_validator("source_event_ids")
    @classmethod
    def validate_source_events(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        return _validate_sorted_unique(value, "source_event_ids")

    @field_validator("explained_terms")
    @classmethod
    def validate_explained_terms(
        cls, value: Tuple[Tuple[str, str], ...]
    ) -> Tuple[Tuple[str, str], ...]:
        if any(not term or not explanation for term, explanation in value):
            raise ValueError("explained_terms pairs must contain non-empty strings")
        terms = tuple(term for term, _ in value)
        if terms != tuple(sorted(terms)) or len(set(terms)) != len(terms):
            raise ValueError("explained_terms must be sorted by unique term")
        return value

    @model_validator(mode="after")
    def validate_projection(self) -> "UserStatusProjection":
        self._validate_visible_scope()
        self._validate_safety_material()
        expected = _expected_model_digest(
            self,
            "agentteams-user-status-projection",
            "projection_sha256",
        )
        if self.projection_sha256 != expected:
            raise ValueError("user status projection digest does not match")
        return self

    def _validate_visible_scope(self) -> None:
        hierarchy_ids = {node.node_id for node in self.hierarchy.nodes}
        base_ids = (self.hierarchy.current_node_id,) + self.hierarchy.direct_child_ids
        if len(set(self.visible_node_ids)) != len(self.visible_node_ids):
            raise ValueError("visible_node_ids must be unique")
        if len(set(self.override_node_ids)) != len(self.override_node_ids):
            raise ValueError("override_node_ids must be unique")
        if any(node_id not in hierarchy_ids for node_id in self.visible_node_ids):
            raise ValueError("visible scope contains an unknown work node")
        if any(node_id not in hierarchy_ids for node_id in self.override_node_ids):
            raise ValueError("override scope contains an unknown work node")
        if set(base_ids).intersection(self.override_node_ids):
            raise ValueError("scope overrides must not repeat current or direct child IDs")

        override_allowed = self.mode in {
            UserMessageMode.RISK,
            UserMessageMode.APPROVAL,
            UserMessageMode.SECURITY,
        }
        if not override_allowed and self.override_node_ids:
            raise ValueError("normal progress/detail projections forbid scope overrides")
        expected_visible = base_ids + (self.override_node_ids if override_allowed else ())
        if self.visible_node_ids != expected_visible:
            raise ValueError(
                "visible scope must contain current and direct child nodes at the allowed depth"
            )

    def _validate_safety_material(self) -> None:
        guardian = self.guardian_decision
        safety = self.safety_decision
        guardian_digest = self.guardian_decision_sha256
        safety_digest = self.safety_decision_sha256
        any_safety = any(
            value is not None
            for value in (guardian, safety, guardian_digest, safety_digest)
        )

        if self.mode in {UserMessageMode.PROGRESS, UserMessageMode.DETAIL} and any_safety:
            raise ValueError("normal progress/detail projections forbid safety overrides")
        if self.mode in {UserMessageMode.APPROVAL, UserMessageMode.SECURITY} and not all(
            value is not None
            for value in (guardian, safety, guardian_digest, safety_digest)
        ):
            raise ValueError("approval/security projections require Guardian and Safety digests")
        if any_safety:
            if not all(
                value is not None
                for value in (guardian, safety, guardian_digest, safety_digest)
            ):
                raise ValueError("partial Guardian/Safety binding is forbidden")
            assert guardian is not None
            assert safety is not None
            if guardian_digest != guardian.decision_sha256:
                raise ValueError("Guardian digest is stale or mismatched")
            if safety_digest != safety.decision_sha256:
                raise ValueError("Safety digest is stale or mismatched")
            if safety.guardian_decision != guardian:
                raise ValueError("Safety decision does not bind the exact Guardian decision")

        if self.mode is UserMessageMode.APPROVAL:
            if safety is None or self.approval_disclosure is None:
                raise ValueError("approval mode requires the exact approval disclosure")
            if safety.approval_disclosure != self.approval_disclosure:
                raise ValueError("approval disclosure is stale or mismatched")
        elif self.approval_disclosure is not None:
            raise ValueError("approval disclosure is forbidden outside approval mode")


__all__ = [
    "ApprovalDisclosure",
    "AttentionFactRef",
    "AttentionPacket",
    "CampaignBinding",
    "CanonicalEffect",
    "EnforcementMode",
    "GuardianDecision",
    "JsonValue",
    "RiskAssessment",
    "RiskDisposition",
    "RiskLevel",
    "RiskStage",
    "SafetyDecision",
    "SafetyVerdict",
    "UserMessageMode",
    "UserStatusProjection",
    "WorkHierarchy",
    "WorkLevel",
    "WorkNode",
]
