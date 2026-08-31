"""Budgeted deterministic evidence capsules without authority-shaped fields."""

from __future__ import annotations

from typing import Literal, Tuple

from pydantic import model_validator

from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256
from benchmarks.secure_memory.models import Digest, StrictModel

from .retrieval import RetrievalResult


class CapsuleItem(StrictModel):
    revision_id: str
    fact_digest: Digest
    statement: str
    evidence_ids: Tuple[str, ...]
    evidence_digests: Tuple[Digest, ...]
    closure_digest: Digest
    provenance_digest: Digest


class EvidenceCapsule(StrictModel):
    schema_version: Literal["egoagentos-evidence-capsule/v1"]
    retrieval_digest: Digest
    items: Tuple[CapsuleItem, ...]
    truncated: bool
    root_digest: Digest

    @model_validator(mode="after")
    def validate_root(self) -> "EvidenceCapsule":
        core = self.model_dump(mode="python", exclude={"root_digest"})
        if self.root_digest != canonical_sha256("trusted-memory-evidence-capsule", core):
            raise ValueError("evidence capsule root mismatch")
        return self


def _capsule(items: Tuple[CapsuleItem, ...], result: RetrievalResult) -> EvidenceCapsule:
    core = {
        "schema_version": "egoagentos-evidence-capsule/v1",
        "retrieval_digest": result.retrieval_digest,
        "items": items,
        "truncated": len(items) < len(result.items),
    }
    return EvidenceCapsule.model_validate(
        {
            **core,
            "root_digest": canonical_sha256("trusted-memory-evidence-capsule", core),
        }
    )


def build_evidence_capsule(
    result: RetrievalResult, *, max_bytes: int, max_items: int
) -> EvidenceCapsule:
    if isinstance(max_bytes, bool) or max_bytes < 256:
        raise ValueError("max_bytes must be at least 256")
    if isinstance(max_items, bool) or max_items < 0:
        raise ValueError("max_items must be non-negative")
    selected: Tuple[CapsuleItem, ...] = ()
    empty = _capsule(selected, result)
    if len(canonical_bytes(empty)) > max_bytes:
        raise ValueError("max_bytes cannot hold an empty capsule")
    for item in result.items[:max_items]:
        candidate_item = CapsuleItem(
            revision_id=item.revision_id,
            fact_digest=item.fact_digest,
            statement=item.statement,
            evidence_ids=item.evidence_ids,
            evidence_digests=item.evidence_digests,
            closure_digest=item.closure_digest,
            provenance_digest=item.provenance_digest,
        )
        candidate = _capsule((*selected, candidate_item), result)
        if len(canonical_bytes(candidate)) > max_bytes:
            break
        selected = candidate.items
    return _capsule(selected, result)


__all__ = ["CapsuleItem", "EvidenceCapsule", "build_evidence_capsule"]
