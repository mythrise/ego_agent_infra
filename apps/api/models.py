"""Typed domain and transport models for the ResearchOps control plane."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


class Stage(str, Enum):
    INTAKE = "INTAKE"
    CONTEXT = "CONTEXT"
    PLAN = "PLAN"
    PLAN_REVIEW = "PLAN_REVIEW"
    APPROVAL = "APPROVAL"
    EXECUTE = "EXECUTE"
    OBSERVE = "OBSERVE"
    EVALUATE = "EVALUATE"
    VERIFY = "VERIFY"
    DECIDE = "DECIDE"
    ARCHIVE = "ARCHIVE"
    MEMORY_SKILL = "MEMORY_SKILL"
    COMPLETED = "COMPLETED"


class RiskLevel(str, Enum):
    R0 = "R0"
    R1 = "R1"
    R2 = "R2"
    R3 = "R3"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"


class ApprovalDecision(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"


class EvidenceKind(str, Enum):
    CODE = "code"
    CONFIG = "config"
    DATASET_MANIFEST = "dataset_manifest"
    LOG = "log"
    METRIC = "metric"
    TRACE = "trace"
    REVIEW = "review"


class GateStatus(str, Enum):
    NOT_RUN = "not_run"
    PASS = "pass"
    FAIL = "fail"


class TerminalDecision(str, Enum):
    KEEP = "KEEP"
    DROP = "DROP"
    INCONCLUSIVE = "INCONCLUSIVE"


class IntegrationTruth(str, Enum):
    READY = "ready"
    NOT_CONFIGURED = "not_configured"
    CONFIGURED_UNVERIFIED = "configured_unverified"
    UNAVAILABLE = "unavailable"


class CandidateArm(StrictModel):
    id: str
    name: str
    description: str


class AcceptanceMetric(StrictModel):
    name: str
    direction: Literal["higher_better", "lower_better"]
    threshold: float
    unit: str
    rule: str


class ResearchGoal(StrictModel):
    objective: str
    frozen: bool = True
    hardware: str
    constraints: Dict[str, Any]
    acceptance_metrics: List[AcceptanceMetric]
    candidate_arms: List[CandidateArm]


class RunManifest(StrictModel):
    git_commit: str
    config_sha256: str
    dataset_manifest_sha256: str
    environment_lock_sha256: str
    base_model_sha256: str
    seed: int

    @field_validator(
        "config_sha256",
        "dataset_manifest_sha256",
        "environment_lock_sha256",
        "base_model_sha256",
    )
    @classmethod
    def validate_digest(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("must be a lowercase SHA-256 digest")
        return value


class LiveSourceBinding(StrictModel):
    """Identity that a live collaboration bridge must match exactly."""

    source: Literal["agentteams"]
    team: str = Field(min_length=1, max_length=160)
    trace_id: str = Field(min_length=8, max_length=200)
    correlation_id: str = Field(min_length=8, max_length=200)
    context_version: int = Field(ge=1)
    origin_authentication: Literal["UNVERIFIED_OPERATOR_ASSERTION"] = (
        "UNVERIFIED_OPERATOR_ASSERTION"
    )


class LiveExecutionContract(StrictModel):
    """Exact action contract shown to a human before a live side effect."""

    action: str = Field(min_length=3, max_length=200)
    config_sha256: str
    action_payload: Dict[str, Any]
    rollback_point: Optional[str] = Field(default=None, min_length=8, max_length=2000)
    approval_ttl_seconds: int = Field(default=900, ge=1, le=900)

    @field_validator("config_sha256")
    @classmethod
    def config_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("config_sha256 must be a lowercase SHA-256 digest")
        return value

    @model_validator(mode="after")
    def payload_matches_contract(self) -> "LiveExecutionContract":
        if self.action_payload.get("config_sha256") != self.config_sha256:
            raise ValueError("action_payload.config_sha256 must match config_sha256")
        if self.action_payload.get("synthetic") is not False:
            raise ValueError("live action_payload.synthetic must be explicitly false")
        return self


class EvaluationResult(StrictModel):
    metric: str
    direction: Literal["higher_better", "lower_better"]
    baseline_mean: float
    candidate_mean: float
    mean_delta: float
    relative_delta: float
    ci95: List[float] = Field(min_length=2, max_length=2)
    threshold: float
    verdict: Literal["PASS", "FAIL", "INCONCLUSIVE"]
    bootstrap_seed: int
    bootstrap_samples: int = Field(ge=100)
    sample_count: int = Field(ge=1)
    data_classification: str

    @model_validator(mode="after")
    def finite_result(self) -> "EvaluationResult":
        values = [
            self.baseline_mean,
            self.candidate_mean,
            self.mean_delta,
            self.relative_delta,
            self.threshold,
            *self.ci95,
        ]
        if any(not math.isfinite(value) for value in values):
            raise ValueError("evaluation results must contain only finite numbers")
        return self


class OfficialReceipt(StrictModel):
    """Digest-bound receipt derived from one real upstream HTTP response."""

    source: Literal["agentteams", "matrix", "gpu", "egoagentos"]
    operation: str = Field(min_length=2, max_length=200)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    endpoint: str = Field(min_length=1, max_length=2000)
    http_status: int = Field(ge=100, le=599)
    request_sha256: str
    response_sha256: str
    response_identifier: Optional[str] = Field(default=None, max_length=1000)

    @field_validator("request_sha256", "response_sha256")
    @classmethod
    def receipt_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("receipt digests must be lowercase SHA-256 values")
        return value


class ExternalArtifactRef(StrictModel):
    uri: str = Field(min_length=1, max_length=4000)
    media_type: str = Field(min_length=1, max_length=200)
    content_sha256: str
    size_bytes: int = Field(ge=0)

    @field_validator("content_sha256")
    @classmethod
    def content_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        return value


class ArtifactEvidencePayload(StrictModel):
    schema_name: Literal["egoagentos.external-artifact-evidence/v1"] = Field(
        default="egoagentos.external-artifact-evidence/v1", alias="schema"
    )
    stage: Literal["CONTEXT", "PLAN", "EXECUTE", "OBSERVE"]
    artifact: ExternalArtifactRef
    receipts: List[OfficialReceipt] = Field(min_length=1)
    attributes: Dict[str, Any] = Field(default_factory=dict)
    synthetic: Literal[False]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class RawMetricSeries(StrictModel):
    baseline: List[float] = Field(min_length=1)
    candidate: List[float] = Field(min_length=1)

    @model_validator(mode="after")
    def paired_samples(self) -> "RawMetricSeries":
        if len(self.baseline) != len(self.candidate):
            raise ValueError("baseline and candidate must contain the same number of samples")
        if any(not math.isfinite(value) for value in self.baseline + self.candidate):
            raise ValueError("raw metric samples must contain only finite numbers")
        return self


class MetricEvidencePayload(StrictModel):
    schema_name: Literal["egoagentos.external-metric-evidence/v1"] = Field(
        default="egoagentos.external-metric-evidence/v1", alias="schema"
    )
    stage: Literal["EVALUATE"]
    artifact: ExternalArtifactRef
    receipts: List[OfficialReceipt] = Field(min_length=1)
    evaluator: str = Field(min_length=2, max_length=200)
    evaluator_sha256: str
    deterministic: Literal[True]
    summary_only: Literal[False]
    raw_samples: Dict[str, RawMetricSeries] = Field(min_length=1)
    raw_metric_digest: str
    results: List[EvaluationResult] = Field(min_length=1)
    attributes: Dict[str, Any] = Field(default_factory=dict)
    synthetic: Literal[False]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("evaluator_sha256", "raw_metric_digest")
    @classmethod
    def metric_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("metric digests must be lowercase SHA-256 values")
        return value


class ReviewEvidencePayload(StrictModel):
    schema_name: Literal["egoagentos.external-review-evidence/v1"] = Field(
        default="egoagentos.external-review-evidence/v1", alias="schema"
    )
    stage: Literal["VERIFY"]
    artifact: ExternalArtifactRef
    receipts: List[OfficialReceipt] = Field(min_length=1)
    reviewer_id: str = Field(min_length=2, max_length=200)
    reviewed_producers: List[str] = Field(min_length=1)
    independent: Literal[True]
    verdict: Literal["PASS", "FAIL"]
    reviewed_evidence_digests: List[str] = Field(min_length=1)
    findings: List[Dict[str, Any]] = Field(default_factory=list)
    attributes: Dict[str, Any] = Field(default_factory=dict)
    synthetic: Literal[False]

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    @field_validator("reviewed_evidence_digests")
    @classmethod
    def reviewed_digests_are_sha256(cls, values: List[str]) -> List[str]:
        normalized = [value.lower() for value in values]
        if any(
            len(value) != 64 or any(char not in "0123456789abcdef" for char in value)
            for value in normalized
        ):
            raise ValueError("reviewed evidence digests must be lowercase SHA-256 values")
        return normalized


ExternalEvidencePayload = Union[
    ArtifactEvidencePayload,
    MetricEvidencePayload,
    ReviewEvidencePayload,
]


class GateResult(StrictModel):
    status: GateStatus
    present: List[EvidenceKind]
    missing: List[EvidenceKind]
    reasons: List[str]
    independent_reviewer: Optional[str] = None
    checked_at: datetime = Field(default_factory=utc_now)


class EvidenceRecord(StrictModel):
    id: str
    task_id: str
    generation: str
    kind: EvidenceKind
    producer_id: str
    artifact_digest: str
    payload: Dict[str, Any]
    synthetic: bool
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("artifact_digest")
    @classmethod
    def artifact_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("artifact_digest must be a SHA-256 digest")
        return value


class ApprovalPublic(StrictModel):
    id: str
    task_id: str
    generation: str
    status: ApprovalStatus
    risk_level: RiskLevel
    scope: str
    action: str
    action_digest: str
    # Optional/defaulted only so databases created before approval-contract v1 remain
    # readable. Every newly built approval supplies both fields; legacy approvals cannot
    # mint a tool-plane token and fail the exact current action-contract comparison.
    config_sha256: Optional[str] = None
    action_payload: Dict[str, Any] = Field(default_factory=dict)
    rollback_point: Optional[str] = None
    requested_at: datetime
    expires_at: datetime
    decided_at: Optional[datetime] = None
    approver: Optional[str] = None
    used_at: Optional[datetime] = None

    @field_validator("action_digest", "config_sha256")
    @classmethod
    def approval_digest_is_sha256(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("approval digests must be lowercase SHA-256 values")
        return value


class ApprovalRecord(ApprovalPublic):
    token_hash: Optional[str] = Field(default=None, exclude=True)


class TaskRecord(StrictModel):
    id: str
    generation: str
    title: str
    objective: str
    stage: Stage
    risk_level: RiskLevel
    goal: ResearchGoal
    scenario: Literal["happy_path", "insufficient_evidence", "external_live"]
    synthetic_demo: bool
    data_notice: str
    owner_agent: str
    current_agent: str
    live_source: Optional[LiveSourceBinding] = None
    execution_contract: Optional[LiveExecutionContract] = None
    version: int = 1
    run_manifest_digest: Optional[str] = None
    latest_evaluation: List[EvaluationResult] = Field(default_factory=list)
    gate_result: GateResult = Field(
        default_factory=lambda: GateResult(
            status=GateStatus.NOT_RUN, present=[], missing=[], reasons=[]
        )
    )
    decision: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def live_task_contract_is_explicit(self) -> "TaskRecord":
        if not self.synthetic_demo:
            if self.scenario != "external_live":
                raise ValueError("non-synthetic tasks must use scenario=external_live")
            if self.live_source is None or self.execution_contract is None:
                raise ValueError(
                    "non-synthetic tasks require live_source and execution_contract"
                )
        return self


class AuditEvent(StrictModel):
    sequence: int
    id: str
    task_id: str
    generation: str
    event_type: str
    actor: str
    stage: Optional[Stage]
    payload: Dict[str, Any]
    previous_hash: str
    event_hash: str
    created_at: datetime


class MemorySignals(StrictModel):
    semantic: float
    component: float
    evidence: float
    recency: float
    failure: float

    @field_validator("semantic", "component", "evidence", "recency", "failure")
    @classmethod
    def bounded(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("memory signal must be in [0, 1]")
        return value


class MemoryCandidate(StrictModel):
    id: str
    task_id: str
    generation: str
    memory_type: Literal["semantic", "episodic", "procedural"]
    statement: str
    component: str
    evidence_digest: str
    review_id: str
    proposed_by: str = "memory-agent"
    status: Literal["candidate"] = "candidate"
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("evidence_digest")
    @classmethod
    def evidence_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("evidence_digest must be a SHA-256 digest")
        return value


class MemoryRecord(StrictModel):
    id: str
    task_id: str
    generation: str
    memory_type: Literal["semantic", "episodic", "procedural"]
    statement: str
    component: str
    evidence_digest: str
    review_id: str
    validated: bool
    candidate_id: Optional[str] = None
    validated_by: Optional[str] = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("evidence_digest")
    @classmethod
    def evidence_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("evidence_digest must be a SHA-256 digest")
        return value


class AdvanceRequest(StrictModel):
    target: Optional[Stage] = None
    approval_token: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class CreateTaskRequest(StrictModel):
    task_id: str = Field(min_length=3, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
    title: str = Field(min_length=3, max_length=300)
    objective: str = Field(min_length=8, max_length=4000)
    synthetic: Literal[False]
    risk_level: RiskLevel
    goal: ResearchGoal
    live_source: LiveSourceBinding
    execution_contract: LiveExecutionContract
    owner_agent: str = Field(default="research-pi", min_length=2, max_length=200)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)

    @model_validator(mode="after")
    def live_risk_contract(self) -> "CreateTaskRequest":
        if self.risk_level not in {RiskLevel.R2, RiskLevel.R3}:
            raise ValueError("live side-effect tasks must be explicitly R2 or R3")
        if self.risk_level == RiskLevel.R3 and not self.execution_contract.rollback_point:
            raise ValueError("R3 live tasks require an explicit rollback_point")
        return self


class EvidenceIngestItem(StrictModel):
    generation: str = Field(min_length=4, max_length=200)
    kind: EvidenceKind
    producer_id: str = Field(min_length=2, max_length=200)
    artifact_digest: str
    payload: ExternalEvidencePayload
    synthetic: Literal[False]

    @field_validator("artifact_digest")
    @classmethod
    def ingest_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("artifact_digest must be a lowercase SHA-256 digest")
        return value


class EvidenceIngestRequest(StrictModel):
    expected_task_version: int = Field(ge=1)
    evidence: EvidenceIngestItem
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class FinalizeTaskRequest(StrictModel):
    generation: str = Field(min_length=4, max_length=200)
    expected_task_version: int = Field(ge=1)
    evidence: List[EvidenceIngestItem] = Field(min_length=1, max_length=32)
    terminal_actor: str = Field(default="research-pi", min_length=2, max_length=200)
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)

    @model_validator(mode="after")
    def evidence_generation_matches(self) -> "FinalizeTaskRequest":
        mismatches = sorted(
            {item.generation for item in self.evidence if item.generation != self.generation}
        )
        if mismatches:
            raise ValueError("all evidence generation values must match request generation")
        return self


class AutorunRequest(StrictModel):
    approval_token: Optional[str] = None
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class ApprovalDecisionRequest(StrictModel):
    decision: ApprovalDecision
    # Compatibility-only assertion. The API derives the effective approver from the
    # authenticated deployment identity and rejects a conflicting caller assertion.
    approver: Optional[str] = Field(
        default=None,
        min_length=2,
        max_length=120,
        json_schema_extra={"deprecated": True},
        description=(
            "Compatibility assertion only; the server derives the effective approver "
            "from the authenticated operator identity"
        ),
    )
    expected_digest: str

    @field_validator("expected_digest")
    @classmethod
    def expected_digest_is_sha256(cls, value: str) -> str:
        value = value.lower()
        if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("expected_digest must be a SHA-256 digest")
        return value


class DemoResetRequest(StrictModel):
    scenario: Literal["happy_path", "insufficient_evidence"] = "happy_path"
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=128)


class RXPVerifyRequest(StrictModel):
    ledger: Dict[str, Any]


class IntegrationState(StrictModel):
    id: str
    name: str
    role: str
    status: IntegrationTruth
    endpoint_configured: bool
    checked_at: datetime
    detail: str
