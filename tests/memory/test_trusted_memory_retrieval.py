from __future__ import annotations

import base64

from apps.api.store import SQLiteStore
from apps.api.trusted_memory.capsule import build_evidence_capsule
from apps.api.trusted_memory.models import (
    DecisionOutcome,
    FactProvenance,
    LegacyMemoryView,
    MemoryOrigin,
    MemoryScope,
    MemoryState,
    TrustedFact,
)
from apps.api.trusted_memory.retrieval import RetrievalQuery, retrieve_exact
from apps.api.trusted_memory.service import TrustedMemoryService
from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256
from benchmarks.secure_memory.models import FactScope, SourceRef, TrustedFactCore


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
DIGEST_C = "c" * 64
DIGEST_D = "d" * 64


def _fact(
    suffix: str,
    *,
    tenant: str = "tenant-a",
    project: str = "project-a",
    component: str = "apps.api",
    version: str = "v1",
    outcome: DecisionOutcome = DecisionOutcome.KEEP,
    origin: MemoryOrigin = MemoryOrigin.LOCAL_TRUSTED,
) -> TrustedFact:
    scope = MemoryScope(tenant_id=tenant, project_id=project, component=component, version=version)
    core = TrustedFactCore(
        schema_version="secure-memory-trusted-fact/v1",
        fact_id=f"fact-{suffix}",
        fact_kind="procedural",
        statement_utf8_base64=base64.b64encode(f"verified statement {suffix}".encode()).decode(),
        outcome=outcome.value,
        applicability_scope=FactScope(
            tenant_id=tenant,
            project_id=project,
            component=component,
            version=version,
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
        origin=origin,
        evaluator_id="sealed-evaluator" if origin is MemoryOrigin.LOCAL_TRUSTED else None,
        evaluator_result_digest=DIGEST_D if origin is MemoryOrigin.LOCAL_TRUSTED else None,
        external_attestation_digest=(
            DIGEST_D if origin is MemoryOrigin.ATTESTED_EXTERNAL else None
        ),
        verified_fact_digests=(digest,),
        evidence_ids=(f"evidence-{suffix}",),
        evidence_digests=(DIGEST_A,),
        policy_version="memory-policy-v1",
        rule_version="memory-rule-v1",
    )
    values = {
        "schema_version": "egoagentos-trusted-memory-fact/v1",
        "revision_id": f"revision-{suffix}",
        "lineage_id": f"lineage-{suffix}",
        "revision": 1,
        "scope": scope,
        "outcome": outcome,
        "origin": origin,
        "state": MemoryState.VALIDATED,
        "core": core,
        "trusted_fact_digest": digest,
        "provenance": provenance,
    }
    return TrustedFact(
        **values,
        record_digest=canonical_sha256("trusted-memory-fact-record", values),
    )


def _query(**overrides):
    values = {
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "component": "apps.api",
        "version": "v1",
        "outcomes": (DecisionOutcome.KEEP,),
        "origins": (MemoryOrigin.LOCAL_TRUSTED,),
    }
    values.update(overrides)
    return RetrievalQuery(**values)


def test_retrieval_applies_every_exact_filter_without_scope_fallback() -> None:
    wanted = _fact("wanted")
    records = (
        _fact("tenant", tenant="tenant-b"),
        _fact("project", project="project-b"),
        _fact("component", component="worker"),
        _fact("version", version="v2"),
        _fact("outcome", outcome=DecisionOutcome.DROP),
        _fact("origin", origin=MemoryOrigin.ATTESTED_EXTERNAL),
        wanted,
    )

    result = retrieve_exact(records, _query())
    assert tuple(item.revision_id for item in result.items) == (wanted.revision_id,)
    assert retrieve_exact(records, _query(version="missing")).items == ()


def test_retrieval_excludes_unverified_and_ineligible_lifecycle_states() -> None:
    eligible = _fact("eligible")
    unverified = LegacyMemoryView(
        legacy_memory_id="legacy-001",
        task_id="task-001",
        generation="1",
        memory_type="procedural",
        statement="legacy validated claim",
        scope=eligible.scope,
        evidence_digest=DIGEST_A,
        review_id="review-001",
        legacy_validated=True,
    )
    ineligible = tuple(
        eligible.model_copy(
            update={"revision_id": f"revision-{state.value.lower()}", "state": state}
        )
        for state in (
            MemoryState.CONFLICTED,
            MemoryState.SUPERSEDED,
            MemoryState.REVOKED,
            MemoryState.EXPIRED,
        )
    )

    result = retrieve_exact((unverified, *ineligible, eligible), _query())
    assert tuple(item.revision_id for item in result.items) == (eligible.revision_id,)


def test_retrieval_ties_and_root_are_stable() -> None:
    first = _fact("a")
    second = _fact("b")

    forward = retrieve_exact((second, first), _query())
    reverse = retrieve_exact((first, second), _query())

    assert forward == reverse
    assert tuple(item.revision_id for item in forward.items) == (
        "revision-a",
        "revision-b",
    )


def test_service_reads_only_explicit_lineages_then_applies_exact_query(tmp_path) -> None:
    store = SQLiteStore(str(tmp_path / "retrieval.sqlite3"))
    wanted = _fact("wanted")
    wrong_component = _fact("wrong", component="worker")
    for fact in (wanted, wrong_component):
        store.append_trusted_memory_record(
            tenant_id=fact.scope.tenant_id,
            project_id=fact.scope.project_id,
            lineage_id=fact.lineage_id,
            record=fact,
            idempotency_key=f"append-{fact.lineage_id}",
        )

    result = TrustedMemoryService(store).retrieve(
        lineage_ids=(wrong_component.lineage_id, wanted.lineage_id),
        query=_query(),
    )

    assert tuple(item.revision_id for item in result.items) == (wanted.revision_id,)


def test_capsule_is_budgeted_deterministic_and_contains_only_evidence_data() -> None:
    result = retrieve_exact(tuple(_fact(str(index)) for index in range(8)), _query())

    first = build_evidence_capsule(result, max_bytes=1300, max_items=3)
    second = build_evidence_capsule(result, max_bytes=1300, max_items=3)
    encoded = canonical_bytes(first)

    assert first == second
    assert len(first.items) <= 3
    assert len(encoded) <= 1300
    assert first.root_digest == canonical_sha256(
        "trusted-memory-evidence-capsule",
        first.model_dump(mode="python", exclude={"root_digest"}),
    )
    assert all(item.evidence_ids and item.closure_digest for item in first.items)
    assert not {"authority", "approval", "token", "secret"}.intersection(
        first.model_dump(mode="python")
    )


def test_capsule_truncation_is_stable_and_does_not_mutate_retrieval() -> None:
    result = retrieve_exact(tuple(_fact(str(index)) for index in range(12)), _query())
    before = canonical_bytes(result)

    small = build_evidence_capsule(result, max_bytes=850, max_items=12)
    again = build_evidence_capsule(result, max_bytes=850, max_items=12)

    assert small == again
    assert small.truncated is True
    assert canonical_bytes(result) == before
