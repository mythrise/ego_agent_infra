"""Small orchestration surface over existing trusted-memory store primitives."""

from __future__ import annotations

from typing import Sequence

from ..store_contract import ResearchStore
from .lifecycle import TrustedLifecycleService
from .models import TrustedFact
from .retrieval import RetrievalQuery, RetrievalResult, retrieve_exact


class TrustedMemoryService:
    def __init__(self, store: ResearchStore) -> None:
        self.store = store
        self.lifecycle = TrustedLifecycleService(store)

    def retrieve(self, *, lineage_ids: Sequence[str], query: RetrievalQuery) -> RetrievalResult:
        facts = []
        for lineage_id in sorted(set(lineage_ids)):
            current = self.store.get_current_trusted_fact(
                tenant_id=query.tenant_id,
                project_id=query.project_id,
                lineage_id=lineage_id,
            )
            if current is not None:
                facts.append(TrustedFact.model_validate_json(current.fact_bytes))
        return retrieve_exact(facts, query)


__all__ = ["TrustedMemoryService"]
