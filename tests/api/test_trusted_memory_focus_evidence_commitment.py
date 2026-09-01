from __future__ import annotations

import base64
from pathlib import Path

from apps.api.store import SQLiteStore
from apps.api.trusted_memory.focus_contracts import FocusMemoryQuery
from apps.api.trusted_memory.focus_service import TrustedMemoryFocusService
from apps.api.trusted_memory.models import (
    DecisionOutcome,
    FactProvenance,
    MemoryOrigin,
    MemoryScope,
    MemoryState,
    TrustedFact,
)
from benchmarks.secure_memory.canonical import canonical_sha256
from benchmarks.secure_memory.models import FactScope, SourceRef, TrustedFactCore


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _trusted_fact() -> TrustedFact:
    scope = MemoryScope(
        tenant_id="tenant-a",
        project_id="project-a",
        component="agentteams-bridge",
        version="v1",
    )
    core = TrustedFactCore(
        schema_version="secure-memory-trusted-fact/v1",
        fact_id="fact-evidence-sets",
        fact_kind="procedural",
        statement_utf8_base64=base64.b64encode(
            b"Preserve the evidence-set commitment without inventing ID/digest pairs."
        ).decode("ascii"),
        outcome=DecisionOutcome.KEEP.value,
        applicability_scope=FactScope(
            tenant_id="tenant-a",
            project_id="project-a",
            component="agentteams-bridge",
            version="v1",
            problem_id="problem-001",
        ),
        source_refs=(
            SourceRef(kind="evidence", identifier="evidence-alpha"),
            SourceRef(kind="evidence", identifier="evidence-zeta"),
        ),
        support_digests=(SHA_A, SHA_D),
    )
    fact_digest = canonical_sha256("trusted-fact", core)
    provenance = FactProvenance(
        schema_version="egoagentos-fact-provenance/v1",
        scope=scope,
        task_id="task-001",
        generation=1,
        task_version=1,
        decision_id="decision-001",
        decision_digest=SHA_B,
        decision_closure_digest=SHA_C,
        origin=MemoryOrigin.LOCAL_TRUSTED,
        evaluator_id="sealed-evaluator",
        evaluator_result_digest=SHA_D,
        external_attestation_digest=None,
        verified_fact_digests=(fact_digest,),
        evidence_ids=("evidence-alpha", "evidence-zeta"),
        evidence_digests=(SHA_A, SHA_D),
        policy_version="memory-policy-v1",
        rule_version="memory-rule-v1",
    )
    values = {
        "schema_version": "egoagentos-trusted-memory-fact/v1",
        "revision_id": "revision-evidence-sets",
        "lineage_id": "lineage-evidence-sets",
        "revision": 1,
        "scope": scope,
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


def test_focus_source_exposes_closure_bound_sets_without_claiming_index_pairing(
    tmp_path: Path,
) -> None:
    store = SQLiteStore(str(tmp_path / "focus-evidence-sets.sqlite3"))
    fact = _trusted_fact()
    store.append_trusted_memory_record(
        tenant_id=fact.scope.tenant_id,
        project_id=fact.scope.project_id,
        lineage_id=fact.lineage_id,
        record=fact,
        idempotency_key="append-evidence-sets",
    )
    result = TrustedMemoryFocusService(store, tenant_id="tenant-a").fetch(
        FocusMemoryQuery(
            tenant_id="tenant-a",
            project_id="project-a",
            outcomes=(DecisionOutcome.KEEP,),
            origins=(MemoryOrigin.LOCAL_TRUSTED,),
            max_items=8,
            scan_limit=32,
        )
    )

    payload = result.facts[0].model_dump(mode="json")
    assert "evidence" not in payload
    commitment = payload["evidence_commitment"]
    assert commitment["association"] == "UNPAIRED_SETS_BOUND_BY_DECISION_CLOSURE"
    assert commitment["evidence_ids"] == ["evidence-alpha", "evidence-zeta"]
    assert commitment["evidence_digests"] == [SHA_A, SHA_D]
    core = {key: value for key, value in commitment.items() if key != "commitment_sha256"}
    assert commitment["commitment_sha256"] == canonical_sha256(
        "trusted-memory-focus-evidence-commitment", core
    )
