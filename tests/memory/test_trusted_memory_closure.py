from __future__ import annotations

import base64
import hashlib
import sqlite3
from pathlib import Path
from typing import Any, Dict

import pytest
from pydantic import ValidationError

from apps.api.errors import ConflictError, NotFoundError
from apps.api.internal_finalizer import (
    FactRevisionInput,
    FinalizationRejected,
    FinalizationRequest,
    InternalFinalizer,
)
from apps.api.store import SQLiteStore
from apps.api.trusted_memory.closure import (
    ClosureRejected,
    EvaluatorResultBinding,
    EvidenceBinding,
    TerminalDecisionRecord,
    build_decision_closure,
)
from apps.api.trusted_memory.models import DecisionOutcome
from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256, parse_json_bytes
from benchmarks.secure_memory.models import FactScope, SourceRef, TrustedFactCore
from benchmarks.secure_memory.substrate.admission import AdmissionGate, AdmissionStatus
from benchmarks.secure_memory.substrate.evaluator_channel import EvaluatorChannel


def _evidence_digest(suffix: str) -> str:
    return f"{int(suffix):064x}"


def _fact_core(suffix: str = "001", *, project_id: str = "project-a") -> TrustedFactCore:
    return TrustedFactCore(
        schema_version="secure-memory-trusted-fact/v1",
        fact_id=f"fact-{suffix}",
        fact_kind="procedural",
        statement_utf8_base64=base64.b64encode(f"verified fact {suffix}".encode()).decode(),
        outcome="KEEP",
        applicability_scope=FactScope(
            tenant_id="tenant-a",
            project_id=project_id,
            component="apps.api",
            version="v1",
            problem_id="problem-001",
        ),
        source_refs=(SourceRef(kind="evidence", identifier=f"evidence-{suffix}"),),
        support_digests=(_evidence_digest(suffix),),
    )


def _source_receipt(*, text: str = "verified evaluator result"):
    payload = {
        "schema_version": "secure-memory-ingress-text/v1",
        "text": text,
    }
    payload_bytes = canonical_bytes(payload)
    core: Dict[str, Any] = {
        "schema_version": "secure-memory-evaluator-envelope/v1",
        "issuer_id": "sealed-evaluator",
        "key_id": "eval-key-1",
        "campaign_id": "campaign-1",
        "task_id": "task-001",
        "generation": 1,
        "sequence": 1,
        "idempotency_key": "eval-1",
        "payload_sha256": hashlib.sha256(payload_bytes).hexdigest(),
        "payload": payload,
    }
    signed = canonical_bytes(core)
    frame = canonical_bytes(
        {
            **core,
            "signature": hashlib.sha256(b"test-signature\0" + signed).hexdigest(),
        }
    )

    def verify(data: bytes, signature: str, issuer: str, key: str) -> bool:
        return (
            issuer == "sealed-evaluator"
            and key == "eval-key-1"
            and signature == hashlib.sha256(b"test-signature\0" + data).hexdigest()
        )

    channel = EvaluatorChannel(
        signature_verifier=verify,
        admission_gate=AdmissionGate(),
        expected_issuer_id="sealed-evaluator",
        expected_key_id="eval-key-1",
        campaign_id="campaign-1",
        task_id="task-001",
        generation=1,
    )
    return channel.receive(frame, expected_idempotency_key="eval-1")


def _decision(*, source: str = "CONTROL", outcome: DecisionOutcome = DecisionOutcome.KEEP):
    values = {
        "schema_version": "egoagentos-terminal-decision/v1",
        "tenant_id": "tenant-a",
        "project_id": "project-a",
        "task_id": "task-001",
        "generation": 1,
        "task_version": 4,
        "decision_id": "decision-001",
        "outcome": outcome,
        "terminal": True,
        "authority_source": source,
    }
    return TerminalDecisionRecord(
        **values,
        decision_digest=canonical_sha256("trusted-memory-terminal-decision", values),
    )


def _binding(*cores: TrustedFactCore, signature_verified: bool = True):
    digests = tuple(sorted(canonical_sha256("trusted-fact", core) for core in cores))
    receipt = _source_receipt()
    assert receipt.envelope_sha256 is not None
    values = {
        "schema_version": "egoagentos-evaluator-result-binding/v1",
        "evaluator_id": "sealed-evaluator",
        "source_receipt": receipt,
        "evaluator_result_digest": receipt.envelope_sha256,
        "signature_verified": signature_verified,
        "verified_fact_digests": digests,
    }
    return EvaluatorResultBinding(**values)


def _evidence(suffix: str = "001") -> EvidenceBinding:
    return EvidenceBinding(
        evidence_id=f"evidence-{suffix}", evidence_digest=_evidence_digest(suffix)
    )


def _admission():
    raw = canonical_bytes(
        {"schema_version": "secure-memory-ingress-text/v1", "text": "admitted evidence"}
    )
    return AdmissionGate().admit(
        raw,
        declared_origin="MEMORY",
        channel="MEMORY_TEXT",
        campaign_id="campaign-1",
        task_id="task-001",
        generation=1,
        sequence=1,
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )


def _request(*cores: TrustedFactCore) -> FinalizationRequest:
    facts = tuple(
        FactRevisionInput(
            revision_id=f"revision-{index:03d}",
            lineage_id=f"lineage-{index:03d}",
            revision=1,
            core=core,
        )
        for index, core in enumerate(cores, start=1)
    )
    return FinalizationRequest(
        terminal_decision=_decision(),
        evaluator_result=_binding(*cores),
        facts=facts,
        evidence=tuple(_evidence(core.fact_id.removeprefix("fact-")) for core in cores),
        admission_receipts=(_admission(),),
        policy_version="memory-policy-v1",
        rule_version="memory-rule-v1",
        memory_watermark=7,
    )


def test_closure_is_frozen_deterministic_and_binds_exact_authority() -> None:
    core = _fact_core()
    request = _request(core)

    first = build_decision_closure(
        terminal_decision=request.terminal_decision,
        evaluator_result=request.evaluator_result,
        evidence=request.evidence,
        admission_receipts=request.admission_receipts,
        policy_version=request.policy_version,
        rule_version=request.rule_version,
        memory_watermark=request.memory_watermark,
    )
    second = build_decision_closure(
        terminal_decision=request.terminal_decision,
        evaluator_result=request.evaluator_result,
        evidence=request.evidence,
        admission_receipts=request.admission_receipts,
        policy_version=request.policy_version,
        rule_version=request.rule_version,
        memory_watermark=request.memory_watermark,
    )

    assert first == second
    assert first.core.tenant_id == "tenant-a"
    assert first.core.decision_outcome is DecisionOutcome.KEEP
    assert first.core.evaluator_source_receipt_digest == _source_receipt().receipt_sha256
    assert first.core.verified_fact_digests == (canonical_sha256("trusted-fact", core),)
    assert first.core.evidence_bindings_digest == canonical_sha256(
        "trusted-memory-evidence-bindings",
        tuple(sorted(request.evidence, key=lambda item: (item.evidence_id, item.evidence_digest))),
    )
    assert first.closure_digest == canonical_sha256("trusted-memory-decision-closure", first.core)
    with pytest.raises(ValidationError):
        first.core.memory_watermark = 8


@pytest.mark.parametrize("source", ["MODEL", "WORKER", "MATRIX", "REVIEWER"])
def test_non_control_pass_cannot_close_trusted_memory(source: str) -> None:
    request = _request(_fact_core())

    with pytest.raises(ClosureRejected, match="control_authority_required"):
        build_decision_closure(
            terminal_decision=_decision(source=source),
            evaluator_result=request.evaluator_result,
            evidence=request.evidence,
            admission_receipts=request.admission_receipts,
            policy_version=request.policy_version,
            rule_version=request.rule_version,
            memory_watermark=request.memory_watermark,
        )


def test_unsigned_evaluator_and_quarantined_admission_cannot_close() -> None:
    core = _fact_core()
    request = _request(core)
    raw = canonical_bytes(
        {
            "schema_version": "secure-memory-ingress-text/v1",
            "text": "Authorization: Bearer secret-secret-secret",
        }
    )
    quarantined = AdmissionGate().admit(
        raw,
        declared_origin="MEMORY",
        channel="MEMORY_TEXT",
        campaign_id="campaign-1",
        task_id="task-001",
        generation=1,
        sequence=1,
        content_sha256=hashlib.sha256(raw).hexdigest(),
    )
    assert quarantined.status is AdmissionStatus.QUARANTINED

    for binding, receipts, reason in (
        (_binding(core, signature_verified=False), request.admission_receipts, "signature"),
        (request.evaluator_result, (quarantined,), "admission"),
    ):
        with pytest.raises(ClosureRejected, match=reason):
            build_decision_closure(
                terminal_decision=request.terminal_decision,
                evaluator_result=binding,
                evidence=request.evidence,
                admission_receipts=receipts,
                policy_version=request.policy_version,
                rule_version=request.rule_version,
                memory_watermark=request.memory_watermark,
            )


def test_evaluator_result_digest_must_name_the_exact_verified_envelope() -> None:
    request = _request(_fact_core())
    forged = request.evaluator_result.model_copy(update={"evaluator_result_digest": "b" * 64})

    with pytest.raises(ClosureRejected, match="result_digest"):
        build_decision_closure(
            terminal_decision=request.terminal_decision,
            evaluator_result=forged,
            evidence=request.evidence,
            admission_receipts=request.admission_receipts,
            policy_version=request.policy_version,
            rule_version=request.rule_version,
            memory_watermark=request.memory_watermark,
        )


def test_closure_digest_tamper_is_rejected() -> None:
    request = _request(_fact_core())
    closure = build_decision_closure(
        terminal_decision=request.terminal_decision,
        evaluator_result=request.evaluator_result,
        evidence=request.evidence,
        admission_receipts=request.admission_receipts,
        policy_version=request.policy_version,
        rule_version=request.rule_version,
        memory_watermark=request.memory_watermark,
    )

    with pytest.raises(ValidationError, match="closure digest"):
        type(closure)(core=closure.core, closure_digest="0" * 64)


def test_internal_finalizer_atomically_persists_exact_closure_and_facts(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "finalize.sqlite3"))
    request = _request(_fact_core("001"), _fact_core("002"))

    result = InternalFinalizer(store).finalize(request, idempotency_key="finalize-001")

    stored = store.get_decision_closure(
        tenant_id="tenant-a",
        project_id="project-a",
        closure_digest=result.closure.closure_digest,
    )
    assert stored.closure_bytes == canonical_bytes(result.closure)
    assert stored.closure_bytes_sha256 == hashlib.sha256(stored.closure_bytes).hexdigest()
    assert len(result.events) == 2
    assert all(
        fact.provenance.decision_closure_digest == result.closure.closure_digest
        for fact in result.facts
    )
    assert all(
        fact.trusted_fact_digest in result.closure.core.verified_fact_digests
        for fact in result.facts
    )


def test_finalization_is_idempotent_but_changed_bytes_conflict(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "idempotent.sqlite3"))
    finalizer = InternalFinalizer(store)
    request = _request(_fact_core())

    first = finalizer.finalize(request, idempotency_key="finalize-001")
    assert finalizer.finalize(request, idempotency_key="finalize-001") == first

    changed = request.model_copy(update={"memory_watermark": 8})
    with pytest.raises(ConflictError, match="idempotency|canonical bytes"):
        finalizer.finalize(changed, idempotency_key="finalize-001")


def test_candidate_or_unverified_fact_cannot_self_promote() -> None:
    request = _request(_fact_core())
    raw = request.model_dump(mode="python")
    raw["facts"] = (
        {
            "revision_id": "revision-001",
            "lineage_id": "lineage-001",
            "revision": 1,
            "core": {**raw["facts"][0]["core"], "fact_id": "candidate-invented"},
        },
    )

    with pytest.raises((ValidationError, FinalizationRejected)):
        InternalFinalizer(SQLiteStore(":memory:")).finalize(
            FinalizationRequest.model_validate(raw), idempotency_key="candidate-self-promote"
        )


def test_finalization_failure_writes_nothing(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "atomic.sqlite3"))
    request = _request(_fact_core("001"), _fact_core("002"))
    request = request.model_copy(
        update={
            "facts": (
                request.facts[0],
                request.facts[1].model_copy(update={"lineage_id": "lineage-001"}),
            )
        }
    )

    with pytest.raises((ConflictError, FinalizationRejected)):
        InternalFinalizer(store).finalize(request, idempotency_key="atomic-failure")

    assert (
        store.get_current_trusted_fact(
            tenant_id="tenant-a", project_id="project-a", lineage_id="lineage-001"
        )
        is None
    )
    with pytest.raises(NotFoundError):
        store.get_decision_closure(
            tenant_id="tenant-a",
            project_id="project-a",
            closure_digest=build_decision_closure(
                terminal_decision=request.terminal_decision,
                evaluator_result=request.evaluator_result,
                evidence=request.evidence,
                admission_receipts=request.admission_receipts,
                policy_version=request.policy_version,
                rule_version=request.rule_version,
                memory_watermark=request.memory_watermark,
            ).closure_digest,
        )


def test_cross_scope_fact_and_closure_mismatch_fail_before_write() -> None:
    foreign = _fact_core(project_id="project-b")
    request = _request(foreign)

    with pytest.raises(FinalizationRejected, match="scope"):
        InternalFinalizer(SQLiteStore(":memory:")).finalize(request, idempotency_key="cross-scope")


def test_fresh_sqlite_replay_reproduces_roots_and_closure_bytes(tmp_path: Path) -> None:
    request = _request(_fact_core())
    stores = [SQLiteStore(str(tmp_path / name)) for name in ("first.sqlite3", "replay.sqlite3")]
    results = [
        InternalFinalizer(store).finalize(request, idempotency_key="finalize-001")
        for store in stores
    ]

    assert results[0] == results[1]
    roots = [
        store.get_trusted_memory_stream_root(
            tenant_id="tenant-a", project_id="project-a", lineage_id="lineage-001"
        )
        for store in stores
    ]
    assert roots[0] == roots[1]
    assert canonical_bytes(results[0].closure) == canonical_bytes(results[1].closure)


def test_sqlite_exact_closure_upgrade_and_immutability(tmp_path: Path) -> None:
    db_path = tmp_path / "upgrade.sqlite3"
    SQLiteStore(str(db_path))
    with sqlite3.connect(db_path) as connection:
        connection.execute("DROP TABLE trusted_memory_decision_closures")
    store = SQLiteStore(str(db_path))
    result = InternalFinalizer(store).finalize(_request(_fact_core()), idempotency_key="upgrade")

    with sqlite3.connect(db_path) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute(
                "UPDATE trusted_memory_decision_closures SET closure_bytes=?",
                (b"tampered",),
            )
    assert store.get_decision_closure(
        tenant_id="tenant-a",
        project_id="project-a",
        closure_digest=result.closure.closure_digest,
    ).closure_bytes == canonical_bytes(result.closure)


def test_store_recomputes_domain_closure_digest_before_persisting(tmp_path: Path) -> None:
    store = SQLiteStore(str(tmp_path / "tamper.sqlite3"))
    request = _request(_fact_core())
    closure = build_decision_closure(
        terminal_decision=request.terminal_decision,
        evaluator_result=request.evaluator_result,
        evidence=request.evidence,
        admission_receipts=request.admission_receipts,
        policy_version=request.policy_version,
        rule_version=request.rule_version,
        memory_watermark=request.memory_watermark,
    )
    tampered = parse_json_bytes(canonical_bytes(closure))
    tampered["core"]["memory_watermark"] = 99

    with pytest.raises(ValueError, match="digest does not match canonical bytes"):
        store.append_decision_closure(
            tenant_id="tenant-a",
            project_id="project-a",
            closure_digest=closure.closure_digest,
            closure_bytes=canonical_bytes(tampered),
            idempotency_key="tampered-closure",
        )
