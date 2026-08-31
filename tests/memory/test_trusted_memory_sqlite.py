from __future__ import annotations

import base64
import sqlite3
from pathlib import Path

import pytest

from apps.api.errors import ConflictError, NotFoundError
from apps.api.models import MemoryRecord
from apps.api.store import SQLiteStore
from apps.api.trusted_memory.models import (
    CandidateFact,
    CandidateProposal,
    ConflictGroup,
    ConflictMember,
    ConflictRecord,
    DecisionOutcome,
    FactProvenance,
    LifecycleTransition,
    LifecycleTransitionCore,
    MemoryOrigin,
    MemoryScope,
    MemoryState,
    RevocationRecord,
    RevocationRecordCore,
    SupersessionRecord,
    SupersessionRecordCore,
    TrustedFact,
    TrustedFactCore,
)
from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256
from benchmarks.secure_memory.models import FactScope, SourceRef


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64
DIGEST_E = "e" * 64


def _scope() -> MemoryScope:
    return MemoryScope(
        tenant_id="tenant-a",
        project_id="project-a",
        component="apps.api",
        version="v1",
    )


def _candidate(candidate_id: str = "candidate-001") -> CandidateFact:
    proposal = CandidateProposal(
        schema_version="secure-memory-candidate/v1",
        proposal_id=candidate_id,
        task_id="task-001",
        generation=1,
        claimed_fact_id="fact-001",
        statement_utf8_base64=base64.b64encode(b"tests passed").decode("ascii"),
        memory_type="procedural",
        component="apps.api",
        outcome_claim="KEEP",
        applicability_scope=FactScope(
            tenant_id="tenant-a",
            project_id="project-a",
            component="apps.api",
            version="v1",
            problem_id="problem-001",
        ),
        source_refs=(SourceRef(kind="evidence", identifier="evidence-001"),),
        support_digest_claims=(DIGEST_A,),
    )
    return CandidateFact(
        schema_version="egoagentos-memory-candidate/v1",
        candidate_id=candidate_id,
        lineage_id="lineage-001",
        revision=1,
        scope=_scope(),
        outcome=DecisionOutcome.KEEP,
        origin=MemoryOrigin.ORIGIN_UNVERIFIED,
        state=MemoryState.CANDIDATE,
        proposal=proposal,
        proposal_digest=canonical_sha256("candidate-proposal", proposal),
    )


def _trusted_fact(*, revision: int = 1, suffix: str = "001") -> TrustedFact:
    core = TrustedFactCore(
        schema_version="secure-memory-trusted-fact/v1",
        fact_id=f"fact-{suffix}",
        fact_kind="procedural",
        statement_utf8_base64=base64.b64encode(f"tests passed {suffix}".encode()).decode("ascii"),
        outcome="KEEP",
        applicability_scope=FactScope(
            tenant_id="tenant-a",
            project_id="project-a",
            component="apps.api",
            version="v1",
            problem_id="problem-001",
        ),
        source_refs=(SourceRef(kind="evidence", identifier=f"evidence-{suffix}"),),
        support_digests=(DIGEST_A,),
    )
    fact_digest = canonical_sha256("trusted-fact", core)
    provenance = FactProvenance(
        schema_version="egoagentos-fact-provenance/v1",
        scope=_scope(),
        task_id="task-001",
        generation=1,
        task_version=4,
        decision_id=f"decision-{suffix}",
        decision_digest=DIGEST_B,
        decision_closure_digest=DIGEST_C,
        origin=MemoryOrigin.LOCAL_TRUSTED,
        evaluator_id="sealed-evaluator",
        evaluator_result_digest=DIGEST_D,
        external_attestation_digest=None,
        verified_fact_digests=(fact_digest,),
        evidence_ids=(f"evidence-{suffix}",),
        evidence_digests=(DIGEST_E,),
        policy_version="memory-policy-v1",
        rule_version="memory-rule-v1",
    )
    values = {
        "schema_version": "egoagentos-trusted-memory-fact/v1",
        "revision_id": f"revision-{suffix}",
        "lineage_id": "lineage-001",
        "revision": revision,
        "scope": _scope(),
        "outcome": DecisionOutcome.KEEP,
        "origin": MemoryOrigin.LOCAL_TRUSTED,
        "state": MemoryState.VALIDATED,
        "core": core,
        "trusted_fact_digest": fact_digest,
        "provenance": provenance,
    }
    return TrustedFact(
        **values,
        record_digest=canonical_sha256("trusted-memory-fact-record", values),
    )


def _lifecycle(fact: TrustedFact, to_state: MemoryState) -> LifecycleTransition:
    core = LifecycleTransitionCore(
        schema_version="egoagentos-memory-lifecycle-transition/v1",
        transition_id=f"transition-{to_state.value.lower()}",
        scope=_scope(),
        lineage_id="lineage-001",
        fact_digest=fact.trusted_fact_digest,
        from_revision=fact.revision,
        to_revision=fact.revision + 1,
        from_state=MemoryState.VALIDATED,
        to_state=to_state,
        actor_role="validator",
        actor_id="memory-validator",
        reason_code=f"FACT_{to_state.value}",
        decision_closure_digest=DIGEST_C,
    )
    return LifecycleTransition(
        core=core,
        transition_digest=canonical_sha256("trusted-memory-lifecycle-transition", core),
    )


def _conflict(fact: TrustedFact) -> ConflictRecord:
    members = (
        ConflictMember(
            scope=_scope(),
            lineage_id="lineage-001",
            revision_id=fact.revision_id,
            revision=fact.revision,
            fact_digest=fact.trusted_fact_digest,
        ),
        ConflictMember(
            scope=_scope(),
            lineage_id="lineage-002",
            revision_id="revision-other",
            revision=1,
            fact_digest=DIGEST_B,
        ),
    )
    group = ConflictGroup(
        schema_version="egoagentos-memory-conflict-group/v1",
        conflict_group_id="conflict-group-001",
        scope=_scope(),
        members=tuple(sorted(members, key=canonical_bytes)),
        reason_code="CONTRADICTORY_EVALUATOR_FACTS",
        decision_closure_digests=(DIGEST_C, DIGEST_D),
    )
    return ConflictRecord(
        group=group,
        conflict_digest=canonical_sha256("trusted-memory-conflict", group),
    )


def _supersession(fact: TrustedFact) -> SupersessionRecord:
    core = SupersessionRecordCore(
        schema_version="egoagentos-memory-supersession/v1",
        supersession_id="supersession-001",
        scope=_scope(),
        lineage_id="lineage-001",
        superseded_revision_id=fact.revision_id,
        superseded_revision=fact.revision,
        superseding_revision_id="revision-002",
        superseding_revision=fact.revision + 1,
        prior_revision_ids=(),
        decision_closure_digest=DIGEST_C,
        reason_code="NEWER_EVALUATOR_FACT",
    )
    return SupersessionRecord(
        core=core,
        supersession_digest=canonical_sha256("trusted-memory-supersession", core),
    )


def _revocation(fact: TrustedFact) -> RevocationRecord:
    core = RevocationRecordCore(
        schema_version="egoagentos-memory-revocation/v1",
        revocation_id="revocation-001",
        scope=_scope(),
        lineage_id="lineage-001",
        revision_id=fact.revision_id,
        revision=fact.revision,
        expected_revision=fact.revision,
        fact_digest=fact.trusted_fact_digest,
        decision_closure_digest=DIGEST_C,
        invalidating_evidence_ids=("evidence-invalidating",),
        invalidating_evidence_digests=(DIGEST_E,),
        reason_code="EVIDENCE_INVALIDATED",
    )
    return RevocationRecord(
        core=core,
        revocation_digest=canonical_sha256("trusted-memory-revocation", core),
    )


def test_candidate_append_creates_history_without_promoting_current(tmp_path: Path) -> None:
    db_path = tmp_path / "memory.sqlite3"
    store = SQLiteStore(str(db_path))
    candidate = _candidate()

    event = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=candidate,
        idempotency_key="candidate-append-001",
    )

    history = store.list_trusted_memory_history(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
    )
    assert history == [event]
    assert event.sequence == 1
    assert event.record_bytes == canonical_bytes(candidate)
    assert event.previous_hash == "0" * 64
    assert (
        store.get_trusted_memory_stream_root(
            tenant_id="tenant-a",
            project_id="project-a",
            lineage_id="lineage-001",
        )
        == event.event_hash
    )
    assert (
        store.get_current_trusted_fact(
            tenant_id="tenant-a",
            project_id="project-a",
            lineage_id="lineage-001",
        )
        is None
    )
    with sqlite3.connect(db_path) as connection:
        assert connection.execute("SELECT count(*) FROM trusted_memory_outbox").fetchone()[0] == 1
        assert connection.execute("SELECT count(*) FROM trusted_memory_closures").fetchone()[0] == 0


def test_identical_idempotency_replays_but_changed_canonical_bytes_conflict(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(str(tmp_path / "memory.sqlite3"))
    candidate = _candidate()
    first = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=candidate,
        idempotency_key="candidate-append-001",
    )

    replay = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=candidate,
        idempotency_key="candidate-append-001",
    )

    assert replay == first
    assert (
        len(
            store.list_trusted_memory_history(
                tenant_id="tenant-a",
                project_id="project-a",
                lineage_id="lineage-001",
            )
        )
        == 1
    )
    with pytest.raises(ConflictError, match="different canonical bytes"):
        store.append_trusted_memory_record(
            tenant_id="tenant-a",
            project_id="project-a",
            lineage_id="lineage-001",
            record=_candidate("candidate-002"),
            idempotency_key="candidate-append-001",
        )


def test_evaluator_closed_fact_cas_updates_current_with_closure_and_outbox(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "memory.sqlite3"
    store = SQLiteStore(str(db_path))
    first_fact = _trusted_fact()

    first = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=first_fact,
        idempotency_key="trusted-fact-001",
    )
    current = store.get_current_trusted_fact(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
    )

    assert current is not None
    assert current.revision_id == "revision-001"
    assert current.fact_bytes == canonical_bytes(first_fact)
    assert current.fact_event_hash == first.event_hash
    assert current.projection_event_hash == first.event_hash
    with sqlite3.connect(db_path) as connection:
        closure = connection.execute(
            "SELECT closure_digest FROM trusted_memory_closures"
        ).fetchone()
        assert closure == (DIGEST_C,)
        assert connection.execute("SELECT count(*) FROM trusted_memory_outbox").fetchone()[0] == 1

    second_fact = _trusted_fact(revision=2, suffix="002")
    with pytest.raises(ConflictError, match="compare-and-swap"):
        store.append_trusted_memory_record(
            tenant_id="tenant-a",
            project_id="project-a",
            lineage_id="lineage-001",
            record=second_fact,
            idempotency_key="trusted-fact-002-stale",
            expected_current_event_hash="f" * 64,
        )
    assert (
        len(
            store.list_trusted_memory_history(
                tenant_id="tenant-a",
                project_id="project-a",
                lineage_id="lineage-001",
            )
        )
        == 1
    )

    second = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=second_fact,
        idempotency_key="trusted-fact-002",
        expected_current_event_hash=first.event_hash,
    )
    current = store.get_current_trusted_fact(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
    )
    assert current is not None
    assert current.revision_id == "revision-002"
    assert current.projection_event_hash == second.event_hash


@pytest.mark.parametrize(
    ("record_factory", "event_type"),
    [
        (lambda fact: _lifecycle(fact, MemoryState.REVOKED), "lifecycle"),
        (_conflict, "conflict"),
        (_revocation, "revocation"),
    ],
)
def test_invalidation_events_cas_current_fact_out_of_eligible_projection(
    tmp_path: Path,
    record_factory,
    event_type: str,
) -> None:
    store = SQLiteStore(str(tmp_path / f"{event_type}.sqlite3"))
    fact = _trusted_fact()
    fact_event = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=fact,
        idempotency_key="trusted-fact-001",
    )

    invalidation = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=record_factory(fact),
        idempotency_key=f"{event_type}-001",
        expected_current_event_hash=fact_event.event_hash,
    )

    assert invalidation.event_type == event_type
    assert (
        store.get_current_trusted_fact(
            tenant_id="tenant-a",
            project_id="project-a",
            lineage_id="lineage-001",
        )
        is None
    )


def test_supersession_makes_old_fact_ineligible_until_exact_next_fact_cas(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(str(tmp_path / "supersession.sqlite3"))
    old_fact = _trusted_fact()
    old_event = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=old_fact,
        idempotency_key="trusted-fact-001",
    )
    supersession = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=_supersession(old_fact),
        idempotency_key="supersession-001",
        expected_current_event_hash=old_event.event_hash,
    )
    assert (
        store.get_current_trusted_fact(
            tenant_id="tenant-a",
            project_id="project-a",
            lineage_id="lineage-001",
        )
        is None
    )

    new_fact = _trusted_fact(revision=2, suffix="002")
    new_event = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=new_fact,
        idempotency_key="trusted-fact-002",
        expected_current_event_hash=supersession.event_hash,
    )
    current = store.get_current_trusted_fact(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
    )
    assert current is not None
    assert current.revision_id == new_fact.revision_id
    assert current.projection_event_hash == new_event.event_hash


def test_history_and_exact_event_queries_are_tenant_project_lineage_scoped(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(str(tmp_path / "scope.sqlite3"))
    event = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=_candidate(),
        idempotency_key="candidate-append-001",
    )

    assert (
        store.get_trusted_memory_event(
            tenant_id="tenant-a",
            project_id="project-a",
            lineage_id="lineage-001",
            event_hash=event.event_hash,
        )
        == event
    )
    assert (
        store.list_trusted_memory_history(
            tenant_id="tenant-b",
            project_id="project-a",
            lineage_id="lineage-001",
        )
        == []
    )
    with pytest.raises(NotFoundError):
        store.get_trusted_memory_event(
            tenant_id="tenant-a",
            project_id="project-b",
            lineage_id="lineage-001",
            event_hash=event.event_hash,
        )


def test_sqlite_guards_history_closure_outbox_and_direct_current_mutation(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "immutable.sqlite3"
    store = SQLiteStore(str(db_path))
    store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=_trusted_fact(),
        idempotency_key="trusted-fact-001",
    )

    with sqlite3.connect(db_path) as connection:
        for table in (
            "trusted_memory_history",
            "trusted_memory_closures",
            "trusted_memory_outbox",
        ):
            with pytest.raises(sqlite3.DatabaseError, match="immutable"):
                connection.execute(f"DELETE FROM {table}")
        with pytest.raises(sqlite3.DatabaseError, match="current projection"):
            connection.execute(
                "UPDATE trusted_memory_current SET projection_event_hash=?",
                ("f" * 64,),
            )


def _replay_history(store: SQLiteStore) -> None:
    old_fact = _trusted_fact()
    old_event = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=old_fact,
        idempotency_key="trusted-fact-001",
    )
    supersession = store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=_supersession(old_fact),
        idempotency_key="supersession-001",
        expected_current_event_hash=old_event.event_hash,
    )
    store.append_trusted_memory_record(
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-001",
        record=_trusted_fact(revision=2, suffix="002"),
        idempotency_key="trusted-fact-002",
        expected_current_event_hash=supersession.event_hash,
    )


def test_fresh_sqlite_replay_reproduces_history_root_and_current_bytes(
    tmp_path: Path,
) -> None:
    first = SQLiteStore(str(tmp_path / "first.sqlite3"))
    replay = SQLiteStore(str(tmp_path / "replay.sqlite3"))
    _replay_history(first)
    _replay_history(replay)

    scope = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "lineage_id": "lineage-001",
    }
    assert first.list_trusted_memory_history(**scope) == replay.list_trusted_memory_history(**scope)
    assert first.get_trusted_memory_stream_root(**scope) == replay.get_trusted_memory_stream_root(
        **scope
    )
    assert first.get_current_trusted_fact(**scope) == replay.get_current_trusted_fact(**scope)
    assert first.verify_trusted_memory_stream(**scope) is True
    assert replay.verify_trusted_memory_stream(**scope) is True


def test_legacy_validated_memory_is_only_an_origin_unverified_view(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "legacy.sqlite3"))
    legacy = MemoryRecord(
        id="legacy-001",
        task_id="task-legacy",
        generation="1",
        memory_type="procedural",
        statement="legacy statement",
        component="apps.api",
        evidence_digest=DIGEST_A,
        review_id="review-legacy",
        validated=True,
        validated_by="legacy-validator",
    )
    store.add_memory(legacy)

    views = store.list_legacy_memory_views(
        task_id="task-legacy",
        generation="1",
        tenant_id="tenant-a",
        project_id="project-a",
        version="legacy-v1",
    )

    assert len(views) == 1
    assert views[0].legacy_validated is True
    assert views[0].origin is MemoryOrigin.ORIGIN_UNVERIFIED
    assert views[0].state is MemoryState.CANDIDATE
    assert (
        store.get_current_trusted_fact(
            tenant_id="tenant-a",
            project_id="project-a",
            lineage_id="legacy-001",
        )
        is None
    )
