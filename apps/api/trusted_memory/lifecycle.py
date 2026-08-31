"""Deterministic lifecycle record builders and strict-CAS append service."""

from __future__ import annotations

from typing import Sequence

from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256

from ..store_contract import ResearchStore, TrustedMemoryEvent
from .models import (
    ConflictGroup,
    ConflictMember,
    ConflictRecord,
    RevocationRecord,
    RevocationRecordCore,
    SupersessionRecord,
    SupersessionRecordCore,
    TrustedFact,
)


def build_conflict(
    *, facts: Sequence[TrustedFact], conflict_group_id: str, reason_code: str
) -> ConflictRecord:
    if len(facts) < 2:
        raise ValueError("a conflict requires at least two facts")
    scope = facts[0].scope
    if any(fact.scope != scope for fact in facts):
        raise ValueError("conflict facts must have the same exact scope")
    members = tuple(
        sorted(
            (
                ConflictMember(
                    scope=fact.scope,
                    lineage_id=fact.lineage_id,
                    revision_id=fact.revision_id,
                    revision=fact.revision,
                    fact_digest=fact.trusted_fact_digest,
                )
                for fact in facts
            ),
            key=canonical_bytes,
        )
    )
    group = ConflictGroup(
        schema_version="egoagentos-memory-conflict-group/v1",
        conflict_group_id=conflict_group_id,
        scope=scope,
        members=members,
        reason_code=reason_code,
        decision_closure_digests=tuple(
            sorted({fact.provenance.decision_closure_digest for fact in facts})
        ),
    )
    return ConflictRecord(
        group=group,
        conflict_digest=canonical_sha256("trusted-memory-conflict", group),
    )


def build_supersession(
    *,
    current: TrustedFact,
    superseding_revision_id: str,
    superseding_revision: int,
    prior_revision_ids: Sequence[str],
    decision_closure_digest: str,
    supersession_id: str,
    reason_code: str,
) -> SupersessionRecord:
    core = SupersessionRecordCore(
        schema_version="egoagentos-memory-supersession/v1",
        supersession_id=supersession_id,
        scope=current.scope,
        lineage_id=current.lineage_id,
        superseded_revision_id=current.revision_id,
        superseded_revision=current.revision,
        superseding_revision_id=superseding_revision_id,
        superseding_revision=superseding_revision,
        prior_revision_ids=tuple(prior_revision_ids),
        decision_closure_digest=decision_closure_digest,
        reason_code=reason_code,
    )
    return SupersessionRecord(
        core=core,
        supersession_digest=canonical_sha256("trusted-memory-supersession", core),
    )


def build_revocation(
    *,
    current: TrustedFact,
    invalidating_evidence_ids: Sequence[str],
    invalidating_evidence_digests: Sequence[str],
    decision_closure_digest: str,
    revocation_id: str,
    reason_code: str,
) -> RevocationRecord:
    core = RevocationRecordCore(
        schema_version="egoagentos-memory-revocation/v1",
        revocation_id=revocation_id,
        scope=current.scope,
        lineage_id=current.lineage_id,
        revision_id=current.revision_id,
        revision=current.revision,
        expected_revision=current.revision,
        fact_digest=current.trusted_fact_digest,
        decision_closure_digest=decision_closure_digest,
        invalidating_evidence_ids=tuple(invalidating_evidence_ids),
        invalidating_evidence_digests=tuple(invalidating_evidence_digests),
        reason_code=reason_code,
    )
    return RevocationRecord(
        core=core,
        revocation_digest=canonical_sha256("trusted-memory-revocation", core),
    )


class TrustedLifecycleService:
    def __init__(self, store: ResearchStore) -> None:
        self.store = store

    def record_conflict(
        self,
        *,
        lineage_id: str,
        record: ConflictRecord,
        expected_current_event_hash: str,
        idempotency_key: str,
    ) -> TrustedMemoryEvent:
        return self.store.append_trusted_memory_record(
            tenant_id=record.group.scope.tenant_id,
            project_id=record.group.scope.project_id,
            lineage_id=lineage_id,
            record=record,
            idempotency_key=idempotency_key,
            expected_current_event_hash=expected_current_event_hash,
        )

    def supersede(
        self,
        *,
        record: SupersessionRecord,
        expected_current_event_hash: str,
        idempotency_key: str,
    ) -> TrustedMemoryEvent:
        return self.store.append_trusted_memory_record(
            tenant_id=record.core.scope.tenant_id,
            project_id=record.core.scope.project_id,
            lineage_id=record.core.lineage_id,
            record=record,
            idempotency_key=idempotency_key,
            expected_current_event_hash=expected_current_event_hash,
        )

    def revoke(
        self,
        *,
        record: RevocationRecord,
        expected_current_event_hash: str,
        idempotency_key: str,
    ) -> TrustedMemoryEvent:
        return self.store.append_trusted_memory_record(
            tenant_id=record.core.scope.tenant_id,
            project_id=record.core.scope.project_id,
            lineage_id=record.core.lineage_id,
            record=record,
            idempotency_key=idempotency_key,
            expected_current_event_hash=expected_current_event_hash,
        )


__all__ = [
    "TrustedLifecycleService",
    "build_conflict",
    "build_revocation",
    "build_supersession",
]
