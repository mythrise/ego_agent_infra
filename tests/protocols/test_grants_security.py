from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from protocols.rxp.errors import RXPError
from protocols.rxp.grants import InMemoryReplayRegistry, SQLiteReplayRegistry
from protocols.rxp.ledger import MatrixLedger
from protocols.rxp.models import ResourceBounds


def test_tampered_grant_signature_is_rejected(first_cell_documents, signer) -> None:  # type: ignore[no-untyped-def]
    _, intent, grant, _ = first_cell_documents
    last = "0" if grant.signature[-1] != "0" else "1"
    tampered = grant.model_copy(update={"signature": grant.signature[:-1] + last})
    with pytest.raises(RXPError, match="grant_signature_invalid"):
        signer.verify(tampered, intent, checked_at="2026-08-29T00:00:03Z")


def test_scope_bound_grant_cannot_authorize_another_intent(first_cell_documents, signer) -> None:  # type: ignore[no-untyped-def]
    _, intent, grant, _ = first_cell_documents
    other = intent.model_copy(update={"scope": "matrix:other:cell:other"})
    with pytest.raises(RXPError, match="grant_scope_mismatch") as error:
        signer.verify(grant, other, checked_at="2026-08-29T00:00:03Z")
    assert "scope" in error.value.details["mismatched_fields"]


def test_expired_grant_is_rejected(first_cell_documents, signer) -> None:  # type: ignore[no-untyped-def]
    _, intent, grant, _ = first_cell_documents
    with pytest.raises(RXPError, match="grant_expired"):
        signer.verify(grant, intent, checked_at="2026-08-29T00:00:12Z")


def test_grant_cannot_be_issued_below_intent_resources(first_cell_documents, signer) -> None:  # type: ignore[no-untyped-def]
    _, intent, _, _ = first_cell_documents
    too_small = ResourceBounds(
        max_gpu_count=0,
        max_wall_time_seconds=1,
        max_gpu_time_seconds=0,
        max_artifact_bytes=1,
    )
    with pytest.raises(RXPError, match="grant_bounds_too_narrow"):
        signer.issue(
            intent,
            grant_id="grant:too-small",
            issuer_id="human:approver",
            bounds=too_small,
            minimum_determinism=intent.required_determinism,
            issued_at="2026-08-29T00:00:02Z",
            expires_at="2026-08-29T00:00:12Z",
            nonce="nonce_too_small_0001",
        )


def test_grant_is_consumed_once_across_concurrent_ledgers(
    first_cell_documents, signer
) -> None:  # type: ignore[no-untyped-def]
    plan, intent, grant, receipt = first_cell_documents
    registry = InMemoryReplayRegistry()
    ledgers = []
    for _ in range(2):
        ledger = MatrixLedger(plan)
        ledger.record_intent(intent)
        ledger.record_grant(
            grant,
            verifier=signer,
            accepted_at="2026-08-29T00:00:03Z",
        )
        ledgers.append(ledger)

    def consume(ledger: MatrixLedger) -> str:
        try:
            ledger.record_receipt(receipt, replay_registry=registry)
            return "ok"
        except RXPError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(consume, ledgers))
    assert results == ["grant_replayed", "ok"]


def test_execution_cannot_start_at_expiry(granted_ledger) -> None:  # type: ignore[no-untyped-def]
    ledger, receipt = granted_ledger
    expired = receipt.model_copy(
        update={
            "started_at": "2026-08-29T00:00:12Z",
            "completed_at": "2026-08-29T00:00:13Z",
        }
    )
    with pytest.raises(RXPError, match="grant_expired"):
        ledger.record_receipt(expired, replay_registry=InMemoryReplayRegistry())


def test_sqlite_registry_is_atomic_across_connections(tmp_path) -> None:  # type: ignore[no-untyped-def]
    path = tmp_path / "replay.sqlite3"
    registries = [SQLiteReplayRegistry(path) for _ in range(32)]

    def consume_once(registry: SQLiteReplayRegistry) -> bool:
        return registry.consume("rxp-grant", "grant:shared:v1")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(consume_once, registries))
    assert results.count(True) == 1
    assert results.count(False) == 31


def test_failed_precondition_does_not_burn_grant(granted_ledger) -> None:  # type: ignore[no-untyped-def]
    ledger, receipt = granted_ledger
    registry = InMemoryReplayRegistry()
    backdated = receipt.model_copy(
        update={
            "started_at": "2026-08-29T00:00:01Z",
            "completed_at": "2026-08-29T00:00:02Z",
        }
    )
    with pytest.raises(RXPError, match="ledger_time_regression"):
        ledger.record_receipt(backdated, replay_registry=registry)
    # Validation happens before the irreversible consume, so the correct Receipt wins.
    ledger.record_receipt(receipt, replay_registry=registry)
