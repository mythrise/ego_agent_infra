"""Exact-scope deterministic retrieval over eligible trusted facts."""

from __future__ import annotations

import base64
from typing import Any, Literal, Sequence, Tuple, Union

from pydantic import Field, field_validator, model_validator

from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import Digest, StrictModel

from .models import (
    DecisionOutcome,
    LegacyMemoryView,
    MemoryOrigin,
    MemoryState,
    TrustedFact,
)


class RetrievalQuery(StrictModel):
    tenant_id: str = Field(min_length=1, max_length=200)
    project_id: str = Field(min_length=1, max_length=200)
    component: str = Field(min_length=1, max_length=200)
    version: str = Field(min_length=1, max_length=120)
    outcomes: Tuple[DecisionOutcome, ...] = Field(min_length=1)
    origins: Tuple[MemoryOrigin, ...] = Field(min_length=1)

    @field_validator("outcomes", "origins")
    @classmethod
    def validate_filters(cls, values: tuple[Any, ...], info: object) -> tuple[Any, ...]:
        encoded = tuple(item.value for item in values)
        if encoded != tuple(sorted(set(encoded))):
            raise ValueError(f"{getattr(info, 'field_name')} must be sorted and unique")
        return values


class RetrievalItem(StrictModel):
    revision_id: str
    lineage_id: str
    fact_digest: Digest
    statement: str
    outcome: DecisionOutcome
    origin: MemoryOrigin
    evidence_ids: Tuple[str, ...]
    evidence_digests: Tuple[Digest, ...]
    closure_digest: Digest
    provenance_digest: Digest


class RetrievalResult(StrictModel):
    schema_version: Literal["egoagentos-retrieval-result/v1"]
    query: RetrievalQuery
    items: Tuple[RetrievalItem, ...]
    retrieval_digest: Digest

    @model_validator(mode="after")
    def validate_digest(self) -> "RetrievalResult":
        core = self.model_dump(mode="python", exclude={"retrieval_digest"})
        if self.retrieval_digest != canonical_sha256("trusted-memory-retrieval", core):
            raise ValueError("retrieval digest mismatch")
        return self


def _item(fact: TrustedFact) -> RetrievalItem:
    statement = base64.b64decode(fact.core.statement_utf8_base64, validate=True).decode("utf-8")
    return RetrievalItem(
        revision_id=fact.revision_id,
        lineage_id=fact.lineage_id,
        fact_digest=fact.trusted_fact_digest,
        statement=statement,
        outcome=fact.outcome,
        origin=fact.origin,
        evidence_ids=fact.provenance.evidence_ids,
        evidence_digests=fact.provenance.evidence_digests,
        closure_digest=fact.provenance.decision_closure_digest,
        provenance_digest=canonical_sha256("trusted-memory-fact-provenance", fact.provenance),
    )


def retrieve_exact(
    records: Sequence[Union[TrustedFact, LegacyMemoryView]], query: RetrievalQuery
) -> RetrievalResult:
    facts = []
    for record in records:
        if not isinstance(record, TrustedFact):
            continue
        if record.state is not MemoryState.VALIDATED:
            continue
        if record.origin not in {MemoryOrigin.LOCAL_TRUSTED, MemoryOrigin.ATTESTED_EXTERNAL}:
            continue
        scope = record.scope
        if (
            scope.tenant_id != query.tenant_id
            or scope.project_id != query.project_id
            or scope.component != query.component
            or scope.version != query.version
            or record.outcome not in query.outcomes
            or record.origin not in query.origins
        ):
            continue
        facts.append(record)
    items = tuple(_item(fact) for fact in sorted(facts, key=lambda fact: fact.revision_id))
    core = {
        "schema_version": "egoagentos-retrieval-result/v1",
        "query": query,
        "items": items,
    }
    return RetrievalResult.model_validate(
        {
            **core,
            "retrieval_digest": canonical_sha256("trusted-memory-retrieval", core),
        }
    )


__all__ = ["RetrievalItem", "RetrievalQuery", "RetrievalResult", "retrieve_exact"]
