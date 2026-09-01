"""Canonical contracts for project-scoped, worker-readable trusted-memory sources."""

from __future__ import annotations

from typing import Any, Literal, Optional, Sequence, Tuple

from pydantic import Field, field_validator, model_validator

from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import Digest, StrictModel

from .models import DecisionOutcome, MemoryOrigin


_TRUSTED_ORIGINS = {
    MemoryOrigin.ATTESTED_EXTERNAL,
    MemoryOrigin.LOCAL_TRUSTED,
}
_EVIDENCE_ASSOCIATION = "UNPAIRED_SETS_BOUND_BY_DECISION_CLOSURE"


class FocusEvidenceCommitment(StrictModel):
    """Evidence sets bound by one closure without inventing index-wise pairings.

    Layer-1 provenance currently stores canonical sets of evidence IDs and evidence
    digests. Their shared DecisionClosure commits to the original evidence bindings,
    but the two public arrays do not preserve a positional relation. The Focus path
    therefore exposes set membership only and directs exact-pair inspection to the
    immutable closure.
    """

    schema_version: Literal["egoagentos-focus-evidence-commitment/v1"]
    association: Literal["UNPAIRED_SETS_BOUND_BY_DECISION_CLOSURE"]
    decision_closure_digest: Digest
    evidence_ids: Tuple[str, ...] = Field(min_length=1)
    evidence_digests: Tuple[Digest, ...] = Field(min_length=1)
    commitment_sha256: Digest

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        if any(not value or len(value) > 200 for value in values):
            raise ValueError("focus evidence IDs must contain 1-200 characters")
        if values != tuple(sorted(set(values))):
            raise ValueError("focus evidence IDs must be sorted and unique")
        return values

    @field_validator("evidence_digests")
    @classmethod
    def validate_evidence_digests(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("focus evidence digests must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_commitment(self) -> "FocusEvidenceCommitment":
        if len(self.evidence_ids) != len(self.evidence_digests):
            raise ValueError("focus evidence ID and digest sets must have equal cardinality")
        core = self.model_dump(mode="python", exclude={"commitment_sha256"})
        expected = canonical_sha256("trusted-memory-focus-evidence-commitment", core)
        if self.commitment_sha256 != expected:
            raise ValueError("focus evidence commitment digest mismatch")
        return self


def build_focus_evidence_commitment(
    *,
    evidence_ids: Sequence[str],
    evidence_digests: Sequence[str],
    decision_closure_digest: str,
) -> FocusEvidenceCommitment:
    """Build a deterministic, non-pairing evidence commitment."""

    ids = tuple(sorted(evidence_ids))
    digests = tuple(sorted(evidence_digests))
    values = {
        "schema_version": "egoagentos-focus-evidence-commitment/v1",
        "association": _EVIDENCE_ASSOCIATION,
        "decision_closure_digest": decision_closure_digest,
        "evidence_ids": ids,
        "evidence_digests": digests,
    }
    return FocusEvidenceCommitment.model_validate(
        {
            **values,
            "commitment_sha256": canonical_sha256(
                "trusted-memory-focus-evidence-commitment", values
            ),
        }
    )


class FocusMemoryQuery(StrictModel):
    """Bounded project-level scan requested by the AgentTeams bridge."""

    tenant_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    outcomes: Tuple[DecisionOutcome, ...] = Field(min_length=1)
    origins: Tuple[MemoryOrigin, ...] = Field(min_length=1)
    max_items: int = Field(default=64, ge=1, le=256)
    scan_limit: int = Field(default=512, ge=1, le=5000)

    @field_validator("outcomes", "origins")
    @classmethod
    def normalize_filters(cls, values: tuple[Any, ...]) -> tuple[Any, ...]:
        unique = {item.value: item for item in values}
        return tuple(unique[key] for key in sorted(unique))

    @model_validator(mode="after")
    def require_trusted_origins(self) -> "FocusMemoryQuery":
        if any(origin not in _TRUSTED_ORIGINS for origin in self.origins):
            raise ValueError("focus-memory queries may request only trusted origins")
        return self


class TrustedFocusFact(StrictModel):
    """A prompt-readable projection of one current, evaluator-closed trusted fact."""

    fact_sha256: Digest
    tenant_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    lineage_id: str = Field(min_length=1, max_length=200)
    revision_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    fact_kind: str = Field(min_length=1, max_length=160)
    statement: str = Field(min_length=1, max_length=4096)
    component: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=120)
    outcome: DecisionOutcome
    origin: MemoryOrigin
    evidence_commitment: FocusEvidenceCommitment
    provenance_sha256: Digest
    projection_event_hash: Digest

    @model_validator(mode="after")
    def validate_trusted_shape(self) -> "TrustedFocusFact":
        if self.origin not in _TRUSTED_ORIGINS:
            raise ValueError("focus facts require a trusted origin")
        if "\x00" in self.statement:
            raise ValueError("focus fact statements cannot contain NUL bytes")
        return self


class TrustedMemoryFocusSource(StrictModel):
    """Digest-bound source snapshot returned by the internal trusted-memory API."""

    schema_version: Literal["egoagentos-trusted-memory-focus-source/v1"]
    query: FocusMemoryQuery
    facts: Tuple[TrustedFocusFact, ...]
    scanned_count: int = Field(ge=0)
    matching_count: int = Field(ge=0)
    truncated_by_scan_limit: bool
    truncated_by_max_items: bool
    memory_snapshot_root: Digest
    source_sha256: Digest

    @model_validator(mode="after")
    def validate_source(self) -> "TrustedMemoryFocusSource":
        if self.scanned_count < self.matching_count:
            raise ValueError("matching_count cannot exceed scanned_count")
        if self.matching_count < len(self.facts):
            raise ValueError("returned facts cannot exceed matching_count")
        if self.truncated_by_max_items != (self.matching_count > len(self.facts)):
            raise ValueError("truncated_by_max_items does not match returned fact count")

        ordered = tuple(sorted(self.facts, key=lambda fact: (fact.fact_sha256, fact.lineage_id)))
        if self.facts != ordered:
            raise ValueError("focus facts must be ordered by fact digest and lineage")
        fact_digests = tuple(fact.fact_sha256 for fact in self.facts)
        lineage_ids = tuple(fact.lineage_id for fact in self.facts)
        if len(fact_digests) != len(set(fact_digests)):
            raise ValueError("focus facts must have unique fact digests")
        if len(lineage_ids) != len(set(lineage_ids)):
            raise ValueError("focus facts must have unique lineage IDs")
        for fact in self.facts:
            if fact.tenant_id != self.query.tenant_id:
                raise ValueError("focus fact tenant does not match source query")
            if fact.project_id != self.query.project_id:
                raise ValueError("focus fact project does not match source query")
            if fact.outcome not in self.query.outcomes:
                raise ValueError("focus fact outcome is outside the source query")
            if fact.origin not in self.query.origins:
                raise ValueError("focus fact origin is outside the source query")

        core = self.model_dump(mode="python", exclude={"source_sha256"})
        expected = canonical_sha256("trusted-memory-focus-source", core)
        if self.source_sha256 != expected:
            raise ValueError("focus source digest does not match its canonical payload")
        return self


def build_trusted_memory_focus_source(
    query: FocusMemoryQuery,
    facts: Sequence[TrustedFocusFact],
    *,
    scanned_count: int,
    truncated_by_scan_limit: bool,
    matching_count: Optional[int] = None,
) -> TrustedMemoryFocusSource:
    """Sort, cap, and bind one deterministic project-level focus source."""

    ordered = tuple(sorted(facts, key=lambda fact: (fact.fact_sha256, fact.lineage_id)))
    if len({fact.fact_sha256 for fact in ordered}) != len(ordered):
        raise ValueError("focus source contains duplicate fact digests")
    if len({fact.lineage_id for fact in ordered}) != len(ordered):
        raise ValueError("focus source contains duplicate lineages")
    for fact in ordered:
        if fact.tenant_id != query.tenant_id or fact.project_id != query.project_id:
            raise ValueError("focus source fact scope does not match query")
        if fact.outcome not in query.outcomes or fact.origin not in query.origins:
            raise ValueError("focus source fact is outside the query filters")

    observed_matching = len(ordered) if matching_count is None else matching_count
    if observed_matching < len(ordered):
        raise ValueError("matching_count cannot be smaller than supplied facts")
    if scanned_count < observed_matching:
        raise ValueError("scanned_count cannot be smaller than matching_count")
    selected = ordered[: query.max_items]
    snapshot_members = tuple(
        (
            fact.lineage_id,
            fact.revision,
            fact.fact_sha256,
            fact.projection_event_hash,
        )
        for fact in ordered
    )
    values = {
        "schema_version": "egoagentos-trusted-memory-focus-source/v1",
        "query": query,
        "facts": selected,
        "scanned_count": scanned_count,
        "matching_count": observed_matching,
        "truncated_by_scan_limit": truncated_by_scan_limit,
        "truncated_by_max_items": observed_matching > len(selected),
        "memory_snapshot_root": canonical_sha256(
            "trusted-memory-focus-snapshot", snapshot_members
        ),
    }
    return TrustedMemoryFocusSource.model_validate(
        {
            **values,
            "source_sha256": canonical_sha256("trusted-memory-focus-source", values),
        }
    )


__all__ = [
    "FocusEvidenceCommitment",
    "FocusMemoryQuery",
    "TrustedFocusFact",
    "TrustedMemoryFocusSource",
    "build_focus_evidence_commitment",
    "build_trusted_memory_focus_source",
]
