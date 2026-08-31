"""Immutable public contracts for evidence-grounded Layer-1 memory.

These models describe data that storage and finalization code may consume later.  They do
not grant authority, perform promotion, or implement persistence/retrieval.
"""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Any, Literal, Mapping, Optional, Tuple

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

from apps.api.models import MemoryRecord
from benchmarks.secure_memory.canonical import (
    canonical_bytes,
    canonical_sha256,
    validate_sha256_digest,
)
from benchmarks.secure_memory.models import CandidateProposal, FactScope, SourceRef, TrustedFactCore


Digest = Annotated[
    str,
    Field(pattern=r"^[0-9a-f]{64}$"),
    AfterValidator(validate_sha256_digest),
]
StableId = Annotated[
    str,
    Field(
        min_length=1,
        max_length=200,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$",
    ),
]
VersionId = Annotated[str, Field(min_length=1, max_length=120)]
ReasonCode = Annotated[
    str,
    Field(min_length=1, max_length=160, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"),
]


_FORBIDDEN_AUTHORITY_KEY_PARTS = ("approval", "capability", "token", "secret")


def _forbidden_authority_key(value: Any) -> Optional[str]:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if isinstance(key, str):
                normalized = key.casefold()
                if any(part in normalized for part in _FORBIDDEN_AUTHORITY_KEY_PARTS):
                    return key
            found = _forbidden_authority_key(nested)
            if found is not None:
                return found
    elif isinstance(value, (list, tuple)):
        for nested in value:
            found = _forbidden_authority_key(nested)
            if found is not None:
                return found
    return None


def _sorted_unique(values: Tuple[str, ...], field_name: str) -> Tuple[str, ...]:
    if values != tuple(sorted(values)) or len(values) != len(set(values)):
        raise ValueError(f"{field_name} must be sorted and duplicate-free")
    return values


def _scope_matches_fact_scope(scope: "MemoryScope", fact_scope: FactScope) -> bool:
    return (
        fact_scope.tenant_id == scope.tenant_id
        and fact_scope.project_id == scope.project_id
        and fact_scope.component == scope.component
        and fact_scope.version == scope.version
    )


class StrictModel(BaseModel):
    """Frozen, closed, canonically representable memory contract."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_default=True)

    @model_validator(mode="before")
    @classmethod
    def reject_authority_fields(cls, value: Any) -> Any:
        found = _forbidden_authority_key(value)
        if found is not None:
            raise ValueError(f"memory data cannot carry authority or secret field: {found}")
        return value

    @model_validator(mode="after")
    def require_canonical_value(self) -> "StrictModel":
        canonical_bytes(self)
        return self


class MemoryOrigin(str, Enum):
    LOCAL_TRUSTED = "LOCAL_TRUSTED"
    ATTESTED_EXTERNAL = "ATTESTED_EXTERNAL"
    ORIGIN_UNVERIFIED = "ORIGIN_UNVERIFIED"
    SYNTHETIC = "SYNTHETIC"
    REVOKED = "REVOKED"


class MemoryState(str, Enum):
    CANDIDATE = "CANDIDATE"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    QUARANTINED = "QUARANTINED"
    CONFLICTED = "CONFLICTED"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    SUPERSEDED = "SUPERSEDED"


class DecisionOutcome(str, Enum):
    KEEP = "KEEP"
    DROP = "DROP"
    INCONCLUSIVE = "INCONCLUSIVE"


class MemoryScope(StrictModel):
    """Exact tenant/project/component/version retrieval and authority boundary."""

    tenant_id: StableId
    project_id: StableId
    component: StableId
    version: VersionId


class FactProvenance(StrictModel):
    """Closure and issuer evidence needed to regard one exact fact core as trusted."""

    schema_version: Literal["egoagentos-fact-provenance/v1"]
    scope: MemoryScope
    task_id: StableId
    generation: int = Field(ge=1)
    task_version: int = Field(ge=1)
    decision_id: StableId
    decision_digest: Digest
    decision_closure_digest: Digest
    origin: MemoryOrigin
    evaluator_id: Optional[StableId]
    evaluator_result_digest: Optional[Digest]
    external_attestation_digest: Optional[Digest]
    verified_fact_digests: Tuple[Digest, ...] = Field(min_length=1)
    evidence_ids: Tuple[StableId, ...] = Field(min_length=1)
    evidence_digests: Tuple[Digest, ...] = Field(min_length=1)
    policy_version: VersionId
    rule_version: VersionId

    @field_validator("verified_fact_digests", "evidence_ids", "evidence_digests")
    @classmethod
    def validate_sorted_ids(cls, values: Tuple[str, ...], info: Any) -> Tuple[str, ...]:
        return _sorted_unique(values, info.field_name)

    @model_validator(mode="after")
    def validate_origin_binding(self) -> "FactProvenance":
        if len(self.evidence_ids) != len(self.evidence_digests):
            raise ValueError("evidence_ids and evidence_digests must have the same length")
        if self.origin is MemoryOrigin.LOCAL_TRUSTED:
            if self.evaluator_id is None or self.evaluator_result_digest is None:
                raise ValueError("LOCAL_TRUSTED provenance requires an evaluator result binding")
            if self.external_attestation_digest is not None:
                raise ValueError(
                    "LOCAL_TRUSTED provenance cannot carry external_attestation_digest"
                )
        elif self.origin is MemoryOrigin.ATTESTED_EXTERNAL:
            if self.external_attestation_digest is None:
                raise ValueError(
                    "ATTESTED_EXTERNAL provenance requires external_attestation_digest"
                )
            if self.evaluator_id is not None or self.evaluator_result_digest is not None:
                raise ValueError("ATTESTED_EXTERNAL provenance cannot claim an evaluator result")
        else:
            raise ValueError(
                "trusted provenance requires LOCAL_TRUSTED or ATTESTED_EXTERNAL origin"
            )
        return self


class CandidateFact(StrictModel):
    """An untrusted proposal; deliberately unable to carry promotion authority."""

    schema_version: Literal["egoagentos-memory-candidate/v1"]
    candidate_id: StableId
    lineage_id: StableId
    revision: int = Field(ge=1)
    scope: MemoryScope
    outcome: DecisionOutcome
    origin: MemoryOrigin
    state: MemoryState
    proposal: CandidateProposal
    proposal_digest: Digest

    @model_validator(mode="after")
    def validate_candidate_claims(self) -> "CandidateFact":
        if self.state is not MemoryState.CANDIDATE:
            raise ValueError("candidate state must remain CANDIDATE")
        if self.origin not in {MemoryOrigin.ORIGIN_UNVERIFIED, MemoryOrigin.SYNTHETIC}:
            raise ValueError("candidate origin must remain ORIGIN_UNVERIFIED or SYNTHETIC")
        if self.candidate_id != self.proposal.proposal_id:
            raise ValueError("candidate_id must match proposal_id")
        if not _scope_matches_fact_scope(self.scope, self.proposal.applicability_scope):
            raise ValueError(
                "candidate tenant/project/component/version scope does not match proposal"
            )
        if self.proposal.component != self.scope.component:
            raise ValueError("candidate component does not match proposal component")
        if self.outcome.value != self.proposal.outcome_claim:
            raise ValueError("candidate outcome does not match proposal outcome claim")
        expected = canonical_sha256("candidate-proposal", self.proposal)
        if self.proposal_digest != expected:
            raise ValueError("proposal_digest does not match the canonical candidate proposal")
        return self


class TrustedFact(StrictModel):
    """One evaluator/external-attestation-closed immutable fact revision."""

    schema_version: Literal["egoagentos-trusted-memory-fact/v1"]
    revision_id: StableId
    lineage_id: StableId
    revision: int = Field(ge=1)
    scope: MemoryScope
    outcome: DecisionOutcome
    origin: MemoryOrigin
    state: MemoryState
    core: TrustedFactCore
    trusted_fact_digest: Digest
    provenance: FactProvenance
    record_digest: Digest

    @model_validator(mode="after")
    def validate_trusted_bindings(self) -> "TrustedFact":
        if self.state is not MemoryState.VALIDATED:
            raise ValueError("trusted fact state must be VALIDATED")
        if self.origin not in {MemoryOrigin.LOCAL_TRUSTED, MemoryOrigin.ATTESTED_EXTERNAL}:
            raise ValueError("trusted fact requires a trusted origin")
        if self.origin is not self.provenance.origin:
            raise ValueError("trusted fact origin does not match provenance origin")
        if self.scope != self.provenance.scope:
            raise ValueError("trusted fact scope does not match provenance scope")
        if not _scope_matches_fact_scope(self.scope, self.core.applicability_scope):
            raise ValueError(
                "trusted fact tenant/project/component/version scope does not match core"
            )
        if self.outcome.value != self.core.outcome:
            raise ValueError("trusted fact outcome does not match core outcome")
        expected_fact = canonical_sha256("trusted-fact", self.core)
        if self.trusted_fact_digest != expected_fact:
            raise ValueError("trusted_fact_digest does not match the canonical trusted fact core")
        if self.trusted_fact_digest not in self.provenance.verified_fact_digests:
            raise ValueError("trusted fact is not named by the bound evaluator/attestation closure")
        expected_record = canonical_sha256(
            "trusted-memory-fact-record",
            self.model_dump(mode="python", exclude={"record_digest"}),
        )
        if self.record_digest != expected_record:
            raise ValueError("record_digest does not match the canonical trusted fact record")
        return self


_ALLOWED_LIFECYCLE_TRANSITIONS = frozenset(
    {
        (MemoryState.CANDIDATE, MemoryState.VALIDATED),
        (MemoryState.CANDIDATE, MemoryState.REJECTED),
        (MemoryState.CANDIDATE, MemoryState.QUARANTINED),
        (MemoryState.CANDIDATE, MemoryState.CONFLICTED),
        (MemoryState.CANDIDATE, MemoryState.EXPIRED),
        (MemoryState.CANDIDATE, MemoryState.REVOKED),
        (MemoryState.VALIDATED, MemoryState.SUPERSEDED),
        (MemoryState.VALIDATED, MemoryState.CONFLICTED),
        (MemoryState.VALIDATED, MemoryState.EXPIRED),
        (MemoryState.VALIDATED, MemoryState.REVOKED),
    }
)


class LifecycleTransitionCore(StrictModel):
    schema_version: Literal["egoagentos-memory-lifecycle-transition/v1"]
    transition_id: StableId
    scope: MemoryScope
    lineage_id: StableId
    fact_digest: Digest
    from_revision: int = Field(ge=1)
    to_revision: int = Field(ge=2)
    from_state: MemoryState
    to_state: MemoryState
    actor_role: Literal["validator"]
    actor_id: StableId
    reason_code: ReasonCode
    decision_closure_digest: Optional[Digest]

    @model_validator(mode="after")
    def validate_transition(self) -> "LifecycleTransitionCore":
        if self.to_revision != self.from_revision + 1:
            raise ValueError("to_revision must be the next revision after from_revision")
        if (self.from_state, self.to_state) not in _ALLOWED_LIFECYCLE_TRANSITIONS:
            raise ValueError("invalid memory lifecycle transition")
        if self.to_state is MemoryState.VALIDATED and self.decision_closure_digest is None:
            raise ValueError("VALIDATED transition requires decision_closure_digest")
        return self


class LifecycleTransition(StrictModel):
    core: LifecycleTransitionCore
    transition_digest: Digest

    @model_validator(mode="after")
    def validate_digest(self) -> "LifecycleTransition":
        expected = canonical_sha256("trusted-memory-lifecycle-transition", self.core)
        if self.transition_digest != expected:
            raise ValueError("transition_digest does not match the canonical transition core")
        return self


class ConflictMember(StrictModel):
    scope: MemoryScope
    lineage_id: StableId
    revision_id: StableId
    revision: int = Field(ge=1)
    fact_digest: Digest


class ConflictGroup(StrictModel):
    schema_version: Literal["egoagentos-memory-conflict-group/v1"]
    conflict_group_id: StableId
    scope: MemoryScope
    members: Tuple[ConflictMember, ...] = Field(min_length=2)
    reason_code: ReasonCode
    decision_closure_digests: Tuple[Digest, ...] = Field(min_length=1)

    @field_validator("decision_closure_digests")
    @classmethod
    def validate_closure_digests(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _sorted_unique(values, "decision_closure_digests")

    @model_validator(mode="after")
    def validate_members(self) -> "ConflictGroup":
        encodings = tuple(canonical_bytes(member) for member in self.members)
        if encodings != tuple(sorted(encodings)) or len(encodings) != len(set(encodings)):
            raise ValueError("conflict members must be canonically sorted and duplicate-free")
        if any(member.scope != self.scope for member in self.members):
            raise ValueError("every conflict member must have the conflict group scope")
        revision_ids = tuple(member.revision_id for member in self.members)
        fact_digests = tuple(member.fact_digest for member in self.members)
        if len(revision_ids) != len(set(revision_ids)):
            raise ValueError("conflict member revision IDs must be duplicate-free")
        if len(fact_digests) != len(set(fact_digests)):
            raise ValueError("conflict member fact digests must be duplicate-free")
        return self


class ConflictRecord(StrictModel):
    group: ConflictGroup
    conflict_digest: Digest

    @model_validator(mode="after")
    def validate_digest(self) -> "ConflictRecord":
        expected = canonical_sha256("trusted-memory-conflict", self.group)
        if self.conflict_digest != expected:
            raise ValueError("conflict_digest does not match the canonical conflict group")
        return self


class SupersessionRecordCore(StrictModel):
    schema_version: Literal["egoagentos-memory-supersession/v1"]
    supersession_id: StableId
    scope: MemoryScope
    lineage_id: StableId
    superseded_revision_id: StableId
    superseded_revision: int = Field(ge=1)
    superseding_revision_id: StableId
    superseding_revision: int = Field(ge=2)
    prior_revision_ids: Tuple[StableId, ...]
    decision_closure_digest: Digest
    reason_code: ReasonCode

    @field_validator("prior_revision_ids")
    @classmethod
    def validate_prior_ids(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        return _sorted_unique(values, "prior_revision_ids")

    @model_validator(mode="after")
    def validate_supersession(self) -> "SupersessionRecordCore":
        if self.superseded_revision_id == self.superseding_revision_id:
            raise ValueError("a revision cannot supersede itself")
        if self.superseding_revision != self.superseded_revision + 1:
            raise ValueError("superseding_revision must be the next revision")
        if (
            self.superseding_revision_id in self.prior_revision_ids
            or self.superseded_revision_id in self.prior_revision_ids
        ):
            raise ValueError("supersession ancestry would create a cycle")
        return self


class SupersessionRecord(StrictModel):
    core: SupersessionRecordCore
    supersession_digest: Digest

    @model_validator(mode="after")
    def validate_digest(self) -> "SupersessionRecord":
        expected = canonical_sha256("trusted-memory-supersession", self.core)
        if self.supersession_digest != expected:
            raise ValueError("supersession_digest does not match the canonical supersession core")
        return self


class RevocationRecordCore(StrictModel):
    schema_version: Literal["egoagentos-memory-revocation/v1"]
    revocation_id: StableId
    scope: MemoryScope
    lineage_id: StableId
    revision_id: StableId
    revision: int = Field(ge=1)
    expected_revision: int = Field(ge=1)
    fact_digest: Digest
    decision_closure_digest: Digest
    invalidating_evidence_ids: Tuple[StableId, ...] = Field(min_length=1)
    invalidating_evidence_digests: Tuple[Digest, ...] = Field(min_length=1)
    reason_code: ReasonCode

    @field_validator("invalidating_evidence_ids", "invalidating_evidence_digests")
    @classmethod
    def validate_evidence(cls, values: Tuple[str, ...], info: Any) -> Tuple[str, ...]:
        return _sorted_unique(values, info.field_name)

    @model_validator(mode="after")
    def validate_revision_binding(self) -> "RevocationRecordCore":
        if self.expected_revision != self.revision:
            raise ValueError("stale revocation expected_revision does not match revision")
        if len(self.invalidating_evidence_ids) != len(self.invalidating_evidence_digests):
            raise ValueError(
                "invalidating_evidence_ids and invalidating_evidence_digests must align"
            )
        return self


class RevocationRecord(StrictModel):
    core: RevocationRecordCore
    revocation_digest: Digest

    @model_validator(mode="after")
    def validate_digest(self) -> "RevocationRecord":
        expected = canonical_sha256("trusted-memory-revocation", self.core)
        if self.revocation_digest != expected:
            raise ValueError("revocation_digest does not match the canonical revocation core")
        return self


class LegacyMemoryView(StrictModel):
    """Read-only compatibility projection; a legacy boolean never establishes trust."""

    schema_version: Literal["egoagentos-legacy-memory-view/v1"] = "egoagentos-legacy-memory-view/v1"
    legacy_memory_id: StableId
    task_id: StableId
    generation: StableId
    memory_type: Literal["semantic", "episodic", "procedural"]
    statement: str = Field(min_length=1, max_length=4096)
    scope: MemoryScope
    evidence_digest: Digest
    review_id: StableId
    legacy_validated: bool
    origin: Literal[MemoryOrigin.ORIGIN_UNVERIFIED] = MemoryOrigin.ORIGIN_UNVERIFIED
    state: Literal[MemoryState.CANDIDATE] = MemoryState.CANDIDATE

    @classmethod
    def from_memory_record(
        cls,
        record: MemoryRecord,
        *,
        tenant_id: str,
        project_id: str,
        version: str,
    ) -> "LegacyMemoryView":
        return cls(
            legacy_memory_id=record.id,
            task_id=record.task_id,
            generation=record.generation,
            memory_type=record.memory_type,
            statement=record.statement,
            scope=MemoryScope(
                tenant_id=tenant_id,
                project_id=project_id,
                component=record.component,
                version=version,
            ),
            evidence_digest=record.evidence_digest,
            review_id=record.review_id,
            legacy_validated=record.validated,
        )


__all__ = [
    "CandidateFact",
    "CandidateProposal",
    "ConflictGroup",
    "ConflictMember",
    "ConflictRecord",
    "DecisionOutcome",
    "Digest",
    "FactProvenance",
    "FactScope",
    "LegacyMemoryView",
    "LifecycleTransition",
    "LifecycleTransitionCore",
    "MemoryOrigin",
    "MemoryScope",
    "MemoryState",
    "RevocationRecord",
    "RevocationRecordCore",
    "SourceRef",
    "StableId",
    "StrictModel",
    "SupersessionRecord",
    "SupersessionRecordCore",
    "TrustedFact",
    "TrustedFactCore",
]
