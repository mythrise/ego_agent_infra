"""Strict RXP/1 protocol document models."""

from __future__ import annotations

import re
from enum import Enum
from itertools import product
from typing import Any, Dict, Literal, Optional, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import canonical_bytes, digest_document

DIGEST_PATTERN = r"^sha256:[0-9a-f]{64}$"
RAW_DIGEST_PATTERN = r"^[0-9a-f]{64}$"
ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}$"
UTC_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


def _validate_json_object(value: Dict[str, Any]) -> Dict[str, Any]:
    canonical_bytes(value)
    return value


class DeterminismLevel(str, Enum):
    """Ordered evidence strength; names are stable protocol values."""

    D0_UNVERIFIED = "D0_UNVERIFIED"
    D1_INPUTS_BOUND = "D1_INPUTS_BOUND"
    D2_SEEDED_ENV_BOUND = "D2_SEEDED_ENV_BOUND"
    D3_BYTE_REPLAY_VERIFIED = "D3_BYTE_REPLAY_VERIFIED"

    @property
    def rank(self) -> int:
        return {
            DeterminismLevel.D0_UNVERIFIED: 0,
            DeterminismLevel.D1_INPUTS_BOUND: 1,
            DeterminismLevel.D2_SEEDED_ENV_BOUND: 2,
            DeterminismLevel.D3_BYTE_REPLAY_VERIFIED: 3,
        }[self]


class CellState(str, Enum):
    INTENT_RECORDED = "INTENT_RECORDED"
    GRANTED = "GRANTED"
    RECEIPT_RECORDED = "RECEIPT_RECORDED"
    EVIDENCE_READY = "EVIDENCE_READY"
    DECIDED = "DECIDED"


class RunManifest(StrictModel):
    git_commit: str = Field(pattern=r"^[0-9a-f]{7,64}$")
    config_sha256: str = Field(pattern=DIGEST_PATTERN)
    dataset_manifest_sha256: str = Field(pattern=DIGEST_PATTERN)
    environment_lock_sha256: str = Field(pattern=DIGEST_PATTERN)
    base_model_sha256: str = Field(pattern=DIGEST_PATTERN)
    seed: int = Field(ge=0, le=2**63 - 1)


class ResourceRequest(StrictModel):
    gpu_count: int = Field(ge=0, le=1024)
    wall_time_seconds: int = Field(ge=1, le=31_536_000)
    gpu_time_seconds: int = Field(ge=0, le=32_292_864_000)
    artifact_bytes: int = Field(ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def gpu_time_is_physically_bounded(self) -> ResourceRequest:
        if self.gpu_time_seconds > self.gpu_count * self.wall_time_seconds:
            raise ValueError("gpu_time_seconds exceeds gpu_count * wall_time_seconds")
        return self


class ResourceBounds(StrictModel):
    max_gpu_count: int = Field(ge=0, le=1024)
    max_wall_time_seconds: int = Field(ge=1, le=31_536_000)
    max_gpu_time_seconds: int = Field(ge=0, le=32_292_864_000)
    max_artifact_bytes: int = Field(ge=0, le=2**63 - 1)

    def contains(self, request: ResourceRequest) -> bool:
        return (
            request.gpu_count <= self.max_gpu_count
            and request.wall_time_seconds <= self.max_wall_time_seconds
            and request.gpu_time_seconds <= self.max_gpu_time_seconds
            and request.artifact_bytes <= self.max_artifact_bytes
        )


class ResourceUsage(StrictModel):
    gpu_count: int = Field(ge=0, le=1024)
    wall_time_seconds: int = Field(ge=0, le=31_536_000)
    gpu_time_seconds: int = Field(ge=0, le=32_292_864_000)
    artifact_bytes: int = Field(ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def gpu_time_is_physically_bounded(self) -> ResourceUsage:
        if self.gpu_time_seconds > self.gpu_count * self.wall_time_seconds:
            raise ValueError("gpu_time_seconds exceeds gpu_count * wall_time_seconds")
        return self


class LegacyApprovalV1Binding(StrictModel):
    jti: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")
    action_digest: str = Field(pattern=RAW_DIGEST_PATTERN)
    config_sha256: str = Field(pattern=RAW_DIGEST_PATTERN)
    token_sha256: str = Field(pattern=RAW_DIGEST_PATTERN)


JsonScalar = Union[str, int, bool]
LedgerEventType = Literal[
    "MATRIX_FROZEN",
    "INTENT_RECORDED",
    "GRANT_RECORDED",
    "RECEIPT_RECORDED",
    "EVIDENCE_RECORDED",
    "DECISION_RECORDED",
]


class MatrixAxis(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
    values: Tuple[JsonScalar, ...] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def values_are_unique(self) -> MatrixAxis:
        encoded = [canonical_bytes(value) for value in self.values]
        if len(encoded) != len(set(encoded)):
            raise ValueError("matrix axis values must be unique")
        return self


class MatrixCellDefinition(StrictModel):
    cell_id: str = Field(pattern=ID_PATTERN)
    coordinates: Dict[str, JsonScalar]


class MatrixPlan(StrictModel):
    rxp_version: Literal["1.0"] = "1.0"
    kind: Literal["MatrixPlan"] = "MatrixPlan"
    matrix_id: str = Field(pattern=ID_PATTERN)
    name: str = Field(min_length=1, max_length=160)
    frozen_by: str = Field(pattern=ID_PATTERN)
    frozen_at: str = Field(pattern=UTC_PATTERN)
    axes: Tuple[MatrixAxis, ...] = Field(min_length=1, max_length=16)
    cells: Tuple[MatrixCellDefinition, ...] = Field(min_length=1, max_length=10_000)
    selection_rule: Literal["CARTESIAN_COMPLETE"] = "CARTESIAN_COMPLETE"

    @model_validator(mode="after")
    def cells_are_the_complete_cartesian_space(self) -> MatrixPlan:
        axis_names = tuple(axis.name for axis in self.axes)
        if tuple(sorted(axis_names)) != axis_names or len(axis_names) != len(set(axis_names)):
            raise ValueError("matrix axes must have unique names in sorted order")
        cell_ids = tuple(cell.cell_id for cell in self.cells)
        if tuple(sorted(cell_ids)) != cell_ids or len(cell_ids) != len(set(cell_ids)):
            raise ValueError("matrix cells must have unique ids in sorted order")
        expected_count = 1
        for axis in self.axes:
            expected_count *= len(axis.values)
            if expected_count > 10_000:
                raise ValueError("matrix Cartesian space exceeds 10,000 cells")
        actual: set[bytes] = set()
        for cell in self.cells:
            if set(cell.coordinates) != set(axis_names):
                raise ValueError("matrix cell coordinates must cover every axis exactly")
            actual.add(canonical_bytes(cell.coordinates))
        expected = {
            canonical_bytes(dict(zip(axis_names, coordinate_values)))
            for coordinate_values in product(*(axis.values for axis in self.axes))
        }
        if len(actual) != len(self.cells) or actual != expected:
            raise ValueError("matrix cells must enumerate the complete Cartesian space")
        return self


class Intent(StrictModel):
    rxp_version: Literal["1.0"] = "1.0"
    kind: Literal["Intent"] = "Intent"
    intent_id: str = Field(pattern=ID_PATTERN)
    matrix_id: str = Field(pattern=ID_PATTERN)
    cell_id: str = Field(pattern=ID_PATTERN)
    coordinates: Dict[str, JsonScalar]
    actor_id: str = Field(pattern=ID_PATTERN)
    created_at: str = Field(pattern=UTC_PATTERN)
    action: str = Field(min_length=1, max_length=128)
    scope: str = Field(min_length=1, max_length=512)
    action_payload: Dict[str, Any]
    action_payload_digest: str = Field(pattern=DIGEST_PATTERN)
    run_manifest: RunManifest
    requested_resources: ResourceRequest
    required_determinism: DeterminismLevel
    approval_v1_binding: Optional[LegacyApprovalV1Binding] = None
    extensions: Dict[str, Any] = Field(default_factory=dict)

    _payload_is_json = field_validator("action_payload")(_validate_json_object)
    _extensions_are_json = field_validator("extensions")(_validate_json_object)

    @model_validator(mode="after")
    def payload_digest_matches(self) -> Intent:
        if digest_document(self.action_payload) != self.action_payload_digest:
            raise ValueError("action_payload_digest does not match canonical action_payload")
        if self.required_determinism.rank >= DeterminismLevel.D2_SEEDED_ENV_BOUND.rank:
            # Presence is guaranteed by RunManifest; this explicit check keeps the
            # semantic rule visible to schema consumers and benchmark adapters.
            if self.run_manifest.seed < 0:
                raise ValueError("D2/D3 intents require a non-negative seed")
        return self


class GrantClaims(StrictModel):
    grant_id: str = Field(pattern=ID_PATTERN)
    issuer_id: str = Field(pattern=ID_PATTERN)
    intent_id: str = Field(pattern=ID_PATTERN)
    intent_digest: str = Field(pattern=DIGEST_PATTERN)
    matrix_id: str = Field(pattern=ID_PATTERN)
    cell_id: str = Field(pattern=ID_PATTERN)
    action: str = Field(min_length=1, max_length=128)
    scope: str = Field(min_length=1, max_length=512)
    action_payload_digest: str = Field(pattern=DIGEST_PATTERN)
    bounds: ResourceBounds
    minimum_determinism: DeterminismLevel
    issued_at: str = Field(pattern=UTC_PATTERN)
    expires_at: str = Field(pattern=UTC_PATTERN)
    nonce: str = Field(pattern=r"^[A-Za-z0-9_-]{16,128}$")
    legacy_approval_v1: Optional[LegacyApprovalV1Binding] = None


class Grant(StrictModel):
    rxp_version: Literal["1.0"] = "1.0"
    kind: Literal["Grant"] = "Grant"
    algorithm: Literal["HMAC-SHA256"] = "HMAC-SHA256"
    key_id: str = Field(pattern=ID_PATTERN)
    claims: GrantClaims
    signature: str = Field(pattern=r"^hmac-sha256:[0-9a-f]{64}$")


class ArtifactRef(StrictModel):
    uri: str = Field(min_length=1, max_length=2048)
    media_type: str = Field(min_length=1, max_length=255)
    sha256: str = Field(pattern=DIGEST_PATTERN)
    bytes: int = Field(ge=0, le=2**63 - 1)


class Receipt(StrictModel):
    rxp_version: Literal["1.0"] = "1.0"
    kind: Literal["Receipt"] = "Receipt"
    receipt_id: str = Field(pattern=ID_PATTERN)
    matrix_id: str = Field(pattern=ID_PATTERN)
    cell_id: str = Field(pattern=ID_PATTERN)
    intent_digest: str = Field(pattern=DIGEST_PATTERN)
    grant_digest: str = Field(pattern=DIGEST_PATTERN)
    grant_id: str = Field(pattern=ID_PATTERN)
    executor_id: str = Field(pattern=ID_PATTERN)
    started_at: str = Field(pattern=UTC_PATTERN)
    completed_at: str = Field(pattern=UTC_PATTERN)
    outcome: Literal["SUCCEEDED", "FAILED"]
    output: ArtifactRef
    usage: ResourceUsage
    determinism_level: DeterminismLevel
    replay_count: int = Field(ge=1, le=1_000_000)
    replay_digest: Optional[str] = Field(default=None, pattern=DIGEST_PATTERN)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    _metadata_is_json = field_validator("metadata")(_validate_json_object)

    @model_validator(mode="after")
    def replay_claim_is_supported(self) -> Receipt:
        if self.determinism_level == DeterminismLevel.D3_BYTE_REPLAY_VERIFIED:
            if self.replay_count < 2 or self.replay_digest != self.output.sha256:
                raise ValueError("D3 requires at least two byte-identical replay outputs")
        return self


class Evidence(StrictModel):
    rxp_version: Literal["1.0"] = "1.0"
    kind: Literal["Evidence"] = "Evidence"
    evidence_id: str = Field(pattern=ID_PATTERN)
    matrix_id: str = Field(pattern=ID_PATTERN)
    cell_id: str = Field(pattern=ID_PATTERN)
    receipt_digest: str = Field(pattern=DIGEST_PATTERN)
    evidence_type: Literal[
        "code", "config", "dataset_manifest", "log", "metric", "trace", "review"
    ]
    producer_id: str = Field(pattern=ID_PATTERN)
    artifact: ArtifactRef
    claims: Dict[str, Any] = Field(default_factory=dict)
    observed_at: str = Field(pattern=UTC_PATTERN)

    _claims_are_json = field_validator("claims")(_validate_json_object)


class GateAssessment(StrictModel):
    status: Literal["PASS", "FAIL"]
    required_types: Tuple[str, ...]
    present_types: Tuple[str, ...]
    missing_types: Tuple[str, ...]
    evidence_digests: Tuple[str, ...]
    evidence_root: str = Field(pattern=DIGEST_PATTERN)
    independent_reviewer: Optional[str] = None
    reasons: Tuple[str, ...] = ()

    @field_validator("evidence_digests")
    @classmethod
    def evidence_digests_are_valid(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        for digest in value:
            if not re.fullmatch(DIGEST_PATTERN, digest):
                raise ValueError("invalid evidence digest")
        return value


class Decision(StrictModel):
    rxp_version: Literal["1.0"] = "1.0"
    kind: Literal["Decision"] = "Decision"
    decision_id: str = Field(pattern=ID_PATTERN)
    matrix_id: str = Field(pattern=ID_PATTERN)
    cell_id: str = Field(pattern=ID_PATTERN)
    intent_digest: str = Field(pattern=DIGEST_PATTERN)
    receipt_digest: str = Field(pattern=DIGEST_PATTERN)
    evidence_digests: Tuple[str, ...]
    evidence_root: str = Field(pattern=DIGEST_PATTERN)
    gate: GateAssessment
    verdict: Literal["KEEP", "REJECT", "INCONCLUSIVE"]
    determinism_level: DeterminismLevel
    decided_by: str = Field(pattern=ID_PATTERN)
    decided_at: str = Field(pattern=UTC_PATTERN)
    rationale_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")

    @model_validator(mode="after")
    def evidence_matches_gate(self) -> Decision:
        if tuple(sorted(self.evidence_digests)) != self.evidence_digests:
            raise ValueError("evidence_digests must be sorted")
        if self.evidence_digests != self.gate.evidence_digests:
            raise ValueError("decision evidence does not match gate assessment")
        if self.evidence_root != self.gate.evidence_root:
            raise ValueError("decision evidence root does not match gate assessment")
        if self.gate.status != "PASS":
            raise ValueError("RXP research decisions require a passing evidence gate")
        return self


class LedgerEntryCore(StrictModel):
    sequence: int = Field(ge=1)
    event_type: LedgerEventType
    matrix_id: str = Field(pattern=ID_PATTERN)
    cell_id: str = Field(pattern=ID_PATTERN)
    document_kind: Literal[
        "MatrixPlan", "Intent", "Grant", "Receipt", "Evidence", "Decision"
    ]
    document_digest: str = Field(pattern=DIGEST_PATTERN)
    document: Dict[str, Any]
    causal_parents: Tuple[str, ...]
    previous_root: str = Field(pattern=DIGEST_PATTERN)
    recorded_at: str = Field(pattern=UTC_PATTERN)

    _document_is_json = field_validator("document")(_validate_json_object)

    @field_validator("causal_parents")
    @classmethod
    def parents_are_digests(cls, value: Tuple[str, ...]) -> Tuple[str, ...]:
        for digest in value:
            if not re.fullmatch(DIGEST_PATTERN, digest):
                raise ValueError("invalid causal parent digest")
        return value


class LedgerEntry(LedgerEntryCore):
    entry_digest: str = Field(pattern=DIGEST_PATTERN)
    root: str = Field(pattern=DIGEST_PATTERN)


class CellSnapshot(StrictModel):
    cell_id: str = Field(pattern=ID_PATTERN)
    state: CellState
    intent_digest: str = Field(pattern=DIGEST_PATTERN)
    grant_digest: Optional[str] = Field(default=None, pattern=DIGEST_PATTERN)
    receipt_digest: Optional[str] = Field(default=None, pattern=DIGEST_PATTERN)
    evidence_digests: Tuple[str, ...] = ()
    decision_digest: Optional[str] = Field(default=None, pattern=DIGEST_PATTERN)
    determinism_level: DeterminismLevel = DeterminismLevel.D0_UNVERIFIED


class MatrixLedgerDocument(StrictModel):
    rxp_version: Literal["1.0"] = "1.0"
    kind: Literal["MatrixLedger"] = "MatrixLedger"
    matrix_id: str = Field(pattern=ID_PATTERN)
    matrix_plan_digest: str = Field(pattern=DIGEST_PATTERN)
    expected_cell_count: int = Field(ge=1, le=10_000)
    decided_cell_count: int = Field(ge=0, le=10_000)
    missing_decisions: Tuple[str, ...]
    completeness: Literal["COMPLETE", "INCOMPLETE"]
    entry_count: int = Field(ge=0)
    root: str = Field(pattern=DIGEST_PATTERN)
    entries: Tuple[LedgerEntry, ...]
    cells: Tuple[CellSnapshot, ...]

    @model_validator(mode="after")
    def count_matches(self) -> MatrixLedgerDocument:
        if self.entry_count != len(self.entries):
            raise ValueError("entry_count does not match entries")
        if tuple(sorted(cell.cell_id for cell in self.cells)) != tuple(
            cell.cell_id for cell in self.cells
        ):
            raise ValueError("cells must be sorted by cell_id")
        if self.decided_cell_count + len(self.missing_decisions) != self.expected_cell_count:
            raise ValueError("matrix completeness counts are inconsistent")
        if tuple(sorted(self.missing_decisions)) != self.missing_decisions:
            raise ValueError("missing_decisions must be sorted")
        expected_status = "COMPLETE" if not self.missing_decisions else "INCOMPLETE"
        if self.completeness != expected_status:
            raise ValueError("matrix completeness status is inconsistent")
        return self


DOCUMENT_MODELS = (
    MatrixPlan,
    Intent,
    Grant,
    Receipt,
    Evidence,
    Decision,
    MatrixLedgerDocument,
)
