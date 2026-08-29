from __future__ import annotations

from datetime import datetime, timezone

import pytest

from protocols.rxp.canonical import canonical_bytes
from protocols.rxp.errors import RXPError
from protocols.rxp.evidence import evidence_gate
from protocols.rxp.grants import InMemoryReplayRegistry, migrate_consumed_approval_v1
from protocols.rxp.models import Evidence, LegacyApprovalV1Binding, ResourceBounds


def _cell_evidence(snapshot):  # type: ignore[no-untyped-def]
    return [
        Evidence.model_validate_json(canonical_bytes(entry.document))
        for entry in snapshot.entries
        if entry.cell_id == "cell-baseline" and entry.document_kind == "Evidence"
    ]


def test_evidence_gate_requires_all_types_and_independent_review(demo_snapshot) -> None:  # type: ignore[no-untyped-def]
    records = _cell_evidence(demo_snapshot)
    assert evidence_gate(records).status == "PASS"
    without_review = [record for record in records if record.evidence_type != "review"]
    assessment = evidence_gate(without_review)
    assert assessment.status == "FAIL"
    assert assessment.missing_types == ("review",)

    forged = [
        record.model_copy(
            update={
                "producer_id": "agent:runtime",
                "claims": {
                    "independent": True,
                    "reviewed_producers": ["agent:runtime"],
                    "reviewer_id": "agent:runtime",
                    "verdict": "PASS",
                },
            }
        )
        if record.evidence_type == "review"
        else record
        for record in records
    ]
    assert evidence_gate(forged).status == "FAIL"


def test_metric_summary_cannot_replace_raw_data(demo_snapshot) -> None:  # type: ignore[no-untyped-def]
    records = _cell_evidence(demo_snapshot)
    summary_only = [
        record.model_copy(
            update={"claims": {"deterministic": True, "summary_only": True}}
        )
        if record.evidence_type == "metric"
        else record
        for record in records
    ]
    assessment = evidence_gate(summary_only)
    assert assessment.status == "FAIL"
    assert "metric evidence requires deterministic raw data" in assessment.reasons


def test_approval_v1_migration_is_exact_and_single_use(
    first_cell_documents, signer
) -> None:  # type: ignore[no-untyped-def]
    _, original_intent, _, _ = first_cell_documents
    token_hash = "a" * 64
    action_digest = "b" * 64
    config_digest = original_intent.run_manifest.config_sha256.removeprefix("sha256:")
    binding = LegacyApprovalV1Binding(
        jti="legacy_migration_0001",
        action_digest=action_digest,
        config_sha256=config_digest,
        token_sha256=token_hash,
    )
    intent = original_intent.model_copy(update={"approval_v1_binding": binding})
    issued = int(
        datetime(2026, 8, 29, 0, 0, 2, tzinfo=timezone.utc).timestamp()
    )
    claims = {
        "jti": binding.jti,
        "action": intent.action,
        "scope": intent.scope,
        "action_digest": action_digest,
        "config_sha256": config_digest,
        "issued_at": issued,
        "expires_at": issued + 10,
    }
    registry = InMemoryReplayRegistry()
    kwargs = {
        "legacy_token_sha256": token_hash,
        "signer": signer,
        "migration_registry": registry,
        "grant_id": "grant:migrated:v1",
        "issuer_id": "gateway:approval-v1",
        "bounds": ResourceBounds(
            max_gpu_count=0,
            max_wall_time_seconds=2,
            max_gpu_time_seconds=0,
            max_artifact_bytes=4096,
        ),
        "minimum_determinism": intent.required_determinism,
        "nonce": "migration_nonce_0001",
    }
    grant = migrate_consumed_approval_v1(intent, claims, **kwargs)
    assert grant.claims.legacy_approval_v1 == binding
    with pytest.raises(RXPError, match="legacy_approval_replayed"):
        migrate_consumed_approval_v1(intent, claims, **kwargs)


def test_approval_v1_migration_rejects_config_mismatch(
    first_cell_documents, signer
) -> None:  # type: ignore[no-untyped-def]
    _, original_intent, _, _ = first_cell_documents
    binding = LegacyApprovalV1Binding(
        jti="legacy_migration_0002",
        action_digest="b" * 64,
        config_sha256="c" * 64,
        token_sha256="a" * 64,
    )
    intent = original_intent.model_copy(update={"approval_v1_binding": binding})
    claims = {
        "jti": binding.jti,
        "action": intent.action,
        "scope": intent.scope,
        "action_digest": binding.action_digest,
        "config_sha256": binding.config_sha256,
        "issued_at": 1787961602,
        "expires_at": 1787961612,
    }
    with pytest.raises(RXPError, match="legacy_config_mismatch"):
        migrate_consumed_approval_v1(
            intent,
            claims,
            legacy_token_sha256=binding.token_sha256,
            signer=signer,
            migration_registry=InMemoryReplayRegistry(),
            grant_id="grant:migrated:v2",
            issuer_id="gateway:approval-v1",
            bounds=ResourceBounds(
                max_gpu_count=0,
                max_wall_time_seconds=2,
                max_gpu_time_seconds=0,
                max_artifact_bytes=4096,
            ),
            minimum_determinism=intent.required_determinism,
            nonce="migration_nonce_0002",
        )
