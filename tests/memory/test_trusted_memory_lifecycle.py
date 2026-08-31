from __future__ import annotations

import base64

import pytest
from pydantic import ValidationError

from apps.api.errors import ConflictError
from apps.api.store import SQLiteStore
from apps.api.trusted_memory.lifecycle import (
    TrustedLifecycleService,
    build_conflict,
    build_revocation,
    build_supersession,
)
from apps.api.trusted_memory.models import (
    DecisionOutcome,
    FactProvenance,
    MemoryOrigin,
    MemoryScope,
    MemoryState,
    TrustedFact,
)
from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256
from benchmarks.secure_memory.models import FactScope, SourceRef, TrustedFactCore


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def _fact(*, lineage: str = "lineage-001", suffix: str = "001", project: str = "project-a"):
    scope = MemoryScope(
        tenant_id="tenant-a", project_id=project, component="apps.api", version="v1"
    )
    core = TrustedFactCore(
        schema_version="secure-memory-trusted-fact/v1",
        fact_id=f"fact-{suffix}",
        fact_kind="procedural",
        statement_utf8_base64=base64.b64encode(f"fact {suffix}".encode()).decode(),
        outcome="KEEP",
        applicability_scope=FactScope(
            tenant_id="tenant-a",
            project_id=project,
            component="apps.api",
            version="v1",
            problem_id="problem-001",
        ),
        source_refs=(SourceRef(kind="evidence", identifier=f"evidence-{suffix}"),),
        support_digests=(DIGEST_A,),
    )
    digest = canonical_sha256("trusted-fact", core)
    provenance = FactProvenance(
        schema_version="egoagentos-fact-provenance/v1",
        scope=scope,
        task_id="task-001",
        generation=1,
        task_version=4,
        decision_id="decision-001",
        decision_digest=DIGEST_B,
        decision_closure_digest=DIGEST_C,
        origin=MemoryOrigin.LOCAL_TRUSTED,
        evaluator_id="sealed-evaluator",
        evaluator_result_digest=DIGEST_D,
        external_attestation_digest=None,
        verified_fact_digests=(digest,),
        evidence_ids=(f"evidence-{suffix}",),
        evidence_digests=(DIGEST_A,),
        policy_version="memory-policy-v1",
        rule_version="memory-rule-v1",
    )
    values = {
        "schema_version": "egoagentos-trusted-memory-fact/v1",
        "revision_id": f"revision-{suffix}",
        "lineage_id": lineage,
        "revision": 1,
        "scope": scope,
        "outcome": DecisionOutcome.KEEP,
        "origin": MemoryOrigin.LOCAL_TRUSTED,
        "state": MemoryState.VALIDATED,
        "core": core,
        "trusted_fact_digest": digest,
        "provenance": provenance,
    }
    return TrustedFact(
        **values,
        record_digest=canonical_sha256("trusted-memory-fact-record", values),
    )


def _append(store: SQLiteStore, fact: TrustedFact):
    return store.append_trusted_memory_record(
        tenant_id=fact.scope.tenant_id,
        project_id=fact.scope.project_id,
        lineage_id=fact.lineage_id,
        record=fact,
        idempotency_key=f"fact-{fact.lineage_id}",
    )


def test_conflict_builder_is_deterministic_and_rejects_cross_scope() -> None:
    first = _fact(lineage="lineage-001", suffix="001")
    second = _fact(lineage="lineage-002", suffix="002")

    a = build_conflict(
        facts=(second, first),
        conflict_group_id="conflict-001",
        reason_code="CONTRADICTORY_FACTS",
    )
    b = build_conflict(
        facts=(first, second),
        conflict_group_id="conflict-001",
        reason_code="CONTRADICTORY_FACTS",
    )
    assert a == b
    assert a.group.members == tuple(sorted(a.group.members, key=canonical_bytes))

    with pytest.raises(ValueError, match="scope"):
        build_conflict(
            facts=(first, _fact(lineage="lineage-003", suffix="003", project="project-b")),
            conflict_group_id="conflict-cross-scope",
            reason_code="CONTRADICTORY_FACTS",
        )


def test_supersession_rejects_cycles_and_stale_revision() -> None:
    fact = _fact()

    with pytest.raises(ValueError, match="cycle"):
        build_supersession(
            current=fact,
            superseding_revision_id="revision-002",
            superseding_revision=2,
            prior_revision_ids=("revision-002",),
            decision_closure_digest=DIGEST_C,
            supersession_id="supersession-cycle",
            reason_code="NEWER_FACT",
        )
    with pytest.raises(ValueError, match="next revision"):
        build_supersession(
            current=fact,
            superseding_revision_id="revision-003",
            superseding_revision=3,
            prior_revision_ids=(),
            decision_closure_digest=DIGEST_C,
            supersession_id="supersession-stale",
            reason_code="NEWER_FACT",
        )


def test_lifecycle_service_applies_conflict_with_strict_cas(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "conflict.sqlite3"))
    first = _fact(lineage="lineage-001", suffix="001")
    second = _fact(lineage="lineage-002", suffix="002")
    first_event = _append(store, first)
    _append(store, second)
    record = build_conflict(
        facts=(first, second),
        conflict_group_id="conflict-001",
        reason_code="CONTRADICTORY_FACTS",
    )
    service = TrustedLifecycleService(store)

    event = service.record_conflict(
        lineage_id=first.lineage_id,
        record=record,
        expected_current_event_hash=first_event.event_hash,
        idempotency_key="conflict-001",
    )
    assert event.event_type == "conflict"
    assert (
        store.get_current_trusted_fact(
            tenant_id="tenant-a", project_id="project-a", lineage_id=first.lineage_id
        )
        is None
    )
    with pytest.raises(ConflictError, match="compare-and-swap"):
        service.record_conflict(
            lineage_id=second.lineage_id,
            record=record,
            expected_current_event_hash="0" * 64,
            idempotency_key="conflict-stale",
        )


def test_supersession_and_revocation_make_only_validated_fact_ineligible(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "lifecycle.sqlite3"))
    old = _fact(lineage="lineage-old", suffix="001")
    revoked = _fact(lineage="lineage-revoked", suffix="002")
    old_event = _append(store, old)
    revoked_event = _append(store, revoked)
    service = TrustedLifecycleService(store)

    supersession = build_supersession(
        current=old,
        superseding_revision_id="revision-next",
        superseding_revision=2,
        prior_revision_ids=(),
        decision_closure_digest=DIGEST_C,
        supersession_id="supersession-001",
        reason_code="NEWER_FACT",
    )
    revocation = build_revocation(
        current=revoked,
        invalidating_evidence_ids=("evidence-invalid",),
        invalidating_evidence_digests=(DIGEST_D,),
        decision_closure_digest=DIGEST_C,
        revocation_id="revocation-001",
        reason_code="EVIDENCE_INVALIDATED",
    )
    service.supersede(
        record=supersession,
        expected_current_event_hash=old_event.event_hash,
        idempotency_key="supersession-001",
    )
    service.revoke(
        record=revocation,
        expected_current_event_hash=revoked_event.event_hash,
        idempotency_key="revocation-001",
    )

    assert (
        store.get_current_trusted_fact(
            tenant_id="tenant-a", project_id="project-a", lineage_id=old.lineage_id
        )
        is None
    )
    assert (
        store.get_current_trusted_fact(
            tenant_id="tenant-a", project_id="project-a", lineage_id=revoked.lineage_id
        )
        is None
    )


def test_revocation_builder_rejects_unsorted_evidence() -> None:
    with pytest.raises(ValidationError, match="sorted"):
        build_revocation(
            current=_fact(),
            invalidating_evidence_ids=("z", "a"),
            invalidating_evidence_digests=(DIGEST_D, DIGEST_A),
            decision_closure_digest=DIGEST_C,
            revocation_id="revocation-unsorted",
            reason_code="EVIDENCE_INVALIDATED",
        )
