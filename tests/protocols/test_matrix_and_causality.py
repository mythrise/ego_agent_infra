from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from protocols.rxp.canonical import canonical_bytes
from protocols.rxp.errors import RXPError
from protocols.rxp.ledger import MatrixLedger, verify_ledger_document
from protocols.rxp.models import Intent, MatrixLedgerDocument, MatrixPlan


def test_matrix_reports_omitted_decisions(first_cell_documents) -> None:  # type: ignore[no-untyped-def]
    plan, intent, _, _ = first_cell_documents
    ledger = MatrixLedger(plan)
    ledger.record_intent(intent)
    snapshot = ledger.snapshot()
    assert snapshot.expected_cell_count == 2
    assert snapshot.decided_cell_count == 0
    assert snapshot.completeness == "INCOMPLETE"
    assert snapshot.missing_decisions == ("cell-baseline", "cell-candidate")
    verify_ledger_document(snapshot)


def test_intent_outside_frozen_matrix_is_rejected(first_cell_documents) -> None:  # type: ignore[no-untyped-def]
    plan, intent, _, _ = first_cell_documents
    ledger = MatrixLedger(plan)
    undeclared = intent.model_copy(update={"cell_id": "cell-hidden"})
    with pytest.raises(RXPError, match="cell_not_declared") as error:
        ledger.record_intent(undeclared)
    assert error.value.code == "cell_not_declared"


def test_matrix_plan_must_enumerate_full_cartesian_space(first_cell_documents) -> None:  # type: ignore[no-untyped-def]
    plan, _, _, _ = first_cell_documents
    data = json.loads(canonical_bytes(plan))
    data["cells"] = data["cells"][:1]
    with pytest.raises(ValidationError, match="complete Cartesian space"):
        MatrixPlan.model_validate_json(json.dumps(data))


def test_missing_seed_is_a_contract_error(first_cell_documents) -> None:  # type: ignore[no-untyped-def]
    _, intent, _, _ = first_cell_documents
    data = json.loads(canonical_bytes(intent))
    del data["run_manifest"]["seed"]
    with pytest.raises(ValidationError):
        Intent.model_validate_json(json.dumps(data))


def test_embedded_document_tampering_is_detected(demo_snapshot) -> None:  # type: ignore[no-untyped-def]
    data = json.loads(canonical_bytes(demo_snapshot))
    evidence_entry = next(
        entry for entry in data["entries"] if entry["document_kind"] == "Evidence"
    )
    evidence_entry["document"]["producer_id"] = "agent:attacker"
    tampered = MatrixLedgerDocument.model_validate_json(json.dumps(data))
    with pytest.raises(RXPError, match="document_digest_mismatch"):
        verify_ledger_document(tampered)


def test_root_tampering_is_detected(demo_snapshot) -> None:  # type: ignore[no-untyped-def]
    tampered = demo_snapshot.model_copy(update={"root": "sha256:" + "0" * 64})
    with pytest.raises(RXPError, match="ledger_snapshot_mismatch"):
        verify_ledger_document(tampered)
