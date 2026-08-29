from __future__ import annotations

from typing import Type, TypeVar

import pytest

from protocols.rxp.canonical import canonical_bytes
from protocols.rxp.demo import DEMO_HMAC_KEY, DEMO_KEY_ID, build_demo_ledger
from protocols.rxp.grants import GrantSigner
from protocols.rxp.ledger import MatrixLedger
from protocols.rxp.models import Grant, Intent, MatrixPlan, Receipt, StrictModel

ModelT = TypeVar("ModelT", bound=StrictModel)


def parse_document(model: Type[ModelT], value: dict) -> ModelT:
    return model.model_validate_json(canonical_bytes(value))


@pytest.fixture
def demo_snapshot():  # type: ignore[no-untyped-def]
    return build_demo_ledger().snapshot()


@pytest.fixture
def first_cell_documents(demo_snapshot):  # type: ignore[no-untyped-def]
    plan = parse_document(MatrixPlan, demo_snapshot.entries[0].document)
    intent_entry = next(
        entry
        for entry in demo_snapshot.entries
        if entry.cell_id == "cell-baseline" and entry.document_kind == "Intent"
    )
    grant_entry = next(
        entry
        for entry in demo_snapshot.entries
        if entry.cell_id == "cell-baseline" and entry.document_kind == "Grant"
    )
    receipt_entry = next(
        entry
        for entry in demo_snapshot.entries
        if entry.cell_id == "cell-baseline" and entry.document_kind == "Receipt"
    )
    return (
        plan,
        parse_document(Intent, intent_entry.document),
        parse_document(Grant, grant_entry.document),
        parse_document(Receipt, receipt_entry.document),
    )


@pytest.fixture
def signer() -> GrantSigner:
    return GrantSigner(DEMO_HMAC_KEY, key_id=DEMO_KEY_ID)


@pytest.fixture
def granted_ledger(first_cell_documents, signer):  # type: ignore[no-untyped-def]
    plan, intent, grant, receipt = first_cell_documents
    ledger = MatrixLedger(plan)
    ledger.record_intent(intent)
    ledger.record_grant(
        grant,
        verifier=signer,
        accepted_at="2026-08-29T00:00:03Z",
    )
    return ledger, receipt
