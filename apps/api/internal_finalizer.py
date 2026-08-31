"""Control-only atomic finalization of evaluator-closed trusted facts."""

from __future__ import annotations

from typing import Literal, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field

from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256
from benchmarks.secure_memory.models import StrictModel, TrustedFactCore
from benchmarks.secure_memory.substrate.admission import AdmissionReceipt

from .store_contract import ResearchStore, TrustedMemoryEvent
from .trusted_memory.closure import (
    DecisionClosure,
    EvaluatorResultBinding,
    EvidenceBinding,
    TerminalDecisionRecord,
    build_decision_closure,
)
from .trusted_memory.models import (
    DecisionOutcome,
    FactProvenance,
    MemoryOrigin,
    MemoryScope,
    MemoryState,
    TrustedFact,
)


class FinalizationRejected(ValueError):
    """Raised before persistence when exact evaluator/decision bindings do not match."""


class FactRevisionInput(StrictModel):
    revision_id: str = Field(min_length=1, max_length=200)
    lineage_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1)
    core: TrustedFactCore
    expected_current_event_hash: Optional[str] = None


class FinalizationRequest(StrictModel):
    terminal_decision: TerminalDecisionRecord
    evaluator_result: EvaluatorResultBinding
    facts: Tuple[FactRevisionInput, ...] = Field(min_length=1)
    evidence: Tuple[EvidenceBinding, ...] = Field(min_length=1)
    admission_receipts: Tuple[AdmissionReceipt, ...] = Field(min_length=1)
    policy_version: str = Field(min_length=1, max_length=120)
    rule_version: str = Field(min_length=1, max_length=120)
    memory_watermark: int = Field(ge=0)


class FinalizationResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["egoagentos-finalization-result/v1"]
    closure: DecisionClosure
    facts: Tuple[TrustedFact, ...]
    events: Tuple[TrustedMemoryEvent, ...]


class InternalFinalizer:
    """Build all records first, then append closure and facts in one transaction."""

    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def _build_facts(
        self, request: FinalizationRequest, closure: DecisionClosure
    ) -> Tuple[TrustedFact, ...]:
        inputs = tuple(sorted(request.facts, key=lambda item: (item.lineage_id, item.revision_id)))
        presented = tuple(canonical_sha256("trusted-fact", item.core) for item in inputs)
        if tuple(sorted(presented)) != closure.core.verified_fact_digests:
            raise FinalizationRejected("facts_not_exactly_named_by_evaluator_result")
        evidence_map = {item.evidence_id: item.evidence_digest for item in request.evidence}
        if len(evidence_map) != len(request.evidence):
            raise FinalizationRejected("duplicate_evidence_id")

        facts = []
        for item, fact_digest in zip(inputs, presented):
            core = item.core
            scope = core.applicability_scope
            if (
                scope.tenant_id != closure.core.tenant_id
                or scope.project_id != closure.core.project_id
            ):
                raise FinalizationRejected("fact_scope_mismatch")
            if scope.version is None:
                raise FinalizationRejected("fact_version_required")
            if core.outcome != closure.core.decision_outcome.value:
                raise FinalizationRejected("fact_outcome_mismatch")
            source_ids = tuple(source.identifier for source in core.source_refs)
            if any(identifier not in evidence_map for identifier in source_ids):
                raise FinalizationRejected("fact_source_evidence_mismatch")
            if any(digest not in evidence_map.values() for digest in core.support_digests):
                raise FinalizationRejected("fact_support_digest_mismatch")
            memory_scope = MemoryScope(
                tenant_id=scope.tenant_id,
                project_id=scope.project_id,
                component=scope.component,
                version=scope.version,
            )
            provenance = FactProvenance(
                schema_version="egoagentos-fact-provenance/v1",
                scope=memory_scope,
                task_id=closure.core.task_id,
                generation=closure.core.generation,
                task_version=closure.core.task_version,
                decision_id=closure.core.decision_id,
                decision_digest=closure.core.decision_digest,
                decision_closure_digest=closure.closure_digest,
                origin=MemoryOrigin.LOCAL_TRUSTED,
                evaluator_id=closure.core.evaluator_id,
                evaluator_result_digest=closure.core.evaluator_result_digest,
                external_attestation_digest=None,
                verified_fact_digests=closure.core.verified_fact_digests,
                evidence_ids=closure.core.evidence_ids,
                evidence_digests=closure.core.evidence_digests,
                policy_version=closure.core.policy_version,
                rule_version=closure.core.rule_version,
            )
            values = {
                "schema_version": "egoagentos-trusted-memory-fact/v1",
                "revision_id": item.revision_id,
                "lineage_id": item.lineage_id,
                "revision": item.revision,
                "scope": memory_scope,
                "outcome": DecisionOutcome(core.outcome),
                "origin": MemoryOrigin.LOCAL_TRUSTED,
                "state": MemoryState.VALIDATED,
                "core": core,
                "trusted_fact_digest": fact_digest,
                "provenance": provenance,
            }
            facts.append(
                TrustedFact.model_validate(
                    {
                        **values,
                        "record_digest": canonical_sha256("trusted-memory-fact-record", values),
                    }
                )
            )
        return tuple(facts)

    def finalize(self, request: FinalizationRequest, *, idempotency_key: str) -> FinalizationResult:
        if not idempotency_key:
            raise ValueError("finalization idempotency key must not be empty")
        closure = build_decision_closure(
            terminal_decision=request.terminal_decision,
            evaluator_result=request.evaluator_result,
            evidence=request.evidence,
            admission_receipts=request.admission_receipts,
            policy_version=request.policy_version,
            rule_version=request.rule_version,
            memory_watermark=request.memory_watermark,
        )
        facts = self._build_facts(request, closure)
        inputs = tuple(sorted(request.facts, key=lambda item: (item.lineage_id, item.revision_id)))
        events = []
        with self.store.transaction():
            self.store.append_decision_closure(
                tenant_id=closure.core.tenant_id,
                project_id=closure.core.project_id,
                closure_digest=closure.closure_digest,
                closure_bytes=canonical_bytes(closure),
                idempotency_key=f"{idempotency_key}:closure",
            )
            for item, fact in zip(inputs, facts):
                events.append(
                    self.store.append_trusted_memory_record(
                        tenant_id=fact.scope.tenant_id,
                        project_id=fact.scope.project_id,
                        lineage_id=fact.lineage_id,
                        record=fact,
                        idempotency_key=f"{idempotency_key}:fact:{fact.lineage_id}",
                        expected_current_event_hash=item.expected_current_event_hash,
                    )
                )
        return FinalizationResult(
            schema_version="egoagentos-finalization-result/v1",
            closure=closure,
            facts=facts,
            events=tuple(events),
        )


__all__ = [
    "FactRevisionInput",
    "FinalizationRejected",
    "FinalizationRequest",
    "FinalizationResult",
    "InternalFinalizer",
]
