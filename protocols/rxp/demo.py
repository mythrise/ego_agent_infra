"""Fully deterministic, explicitly synthetic RXP reference replay."""

from __future__ import annotations

from .canonical import canonical_bytes, digest_document, sha256_bytes
from .grants import GrantSigner, InMemoryReplayRegistry
from .ledger import MatrixLedger
from .models import (
    ArtifactRef,
    Decision,
    DeterminismLevel,
    Evidence,
    Intent,
    MatrixAxis,
    MatrixCellDefinition,
    MatrixPlan,
    Receipt,
    ResourceBounds,
    ResourceRequest,
    ResourceUsage,
    RunManifest,
)

# Public fixture material: never use this key outside the synthetic demo/tests.
DEMO_HMAC_KEY = b"RXP SYNTHETIC DEMO KEY - NOT A SECRET - 00000000000000000000"
DEMO_KEY_ID = "demo-key-do-not-use"


def _digest(label: str) -> str:
    return sha256_bytes(label.encode("utf-8"))


def build_demo_ledger() -> MatrixLedger:
    """Build a complete two-cell matrix with fixed, byte-replayable documents."""

    plan = MatrixPlan(
        matrix_id="matrix:synthetic-ablation-v1",
        name="Synthetic two-arm determinism fixture",
        frozen_by="agent:research-pi",
        frozen_at="2026-08-29T00:00:00Z",
        axes=(MatrixAxis(name="arm", values=("baseline", "candidate")),),
        cells=(
            MatrixCellDefinition(
                cell_id="cell-baseline", coordinates={"arm": "baseline"}
            ),
            MatrixCellDefinition(
                cell_id="cell-candidate", coordinates={"arm": "candidate"}
            ),
        ),
    )
    ledger = MatrixLedger(plan)
    signer = GrantSigner(DEMO_HMAC_KEY, key_id=DEMO_KEY_ID)
    replay_registry = InMemoryReplayRegistry()
    for index, (cell_id, arm, metric_milli) in enumerate(
        (
            ("cell-baseline", "baseline", 712),
            ("cell-candidate", "candidate", 741),
        )
    ):
        offset = index * 20
        payload = {"arm": arm, "entrypoint": "synthetic.evaluate", "repetitions": 2}
        intent = Intent(
            intent_id=f"intent:{arm}:v1",
            matrix_id=plan.matrix_id,
            cell_id=cell_id,
            coordinates={"arm": arm},
            actor_id="agent:experiment-architect",
            created_at=f"2026-08-29T00:00:{offset + 1:02d}Z",
            action="experiment.execute",
            scope=f"matrix:{plan.matrix_id}:cell:{cell_id}",
            action_payload=payload,
            action_payload_digest=digest_document(payload),
            run_manifest=RunManifest(
                git_commit="0123456789abcdef0123456789abcdef01234567",
                config_sha256=_digest(f"config:{arm}"),
                dataset_manifest_sha256=_digest("dataset:synthetic-v1"),
                environment_lock_sha256=_digest("environment:fixture-v1"),
                base_model_sha256=_digest("model:none"),
                seed=20260829,
            ),
            requested_resources=ResourceRequest(
                gpu_count=0,
                wall_time_seconds=1,
                gpu_time_seconds=0,
                artifact_bytes=4096,
            ),
            required_determinism=DeterminismLevel.D3_BYTE_REPLAY_VERIFIED,
            extensions={"data_classification": "SYNTHETIC_FIXTURE"},
        )
        intent_digest = ledger.record_intent(intent)
        bounds = ResourceBounds(
            max_gpu_count=0,
            max_wall_time_seconds=2,
            max_gpu_time_seconds=0,
            max_artifact_bytes=4096,
        )
        issued_at = f"2026-08-29T00:00:{offset + 2:02d}Z"
        grant = signer.issue(
            intent,
            grant_id=f"grant:{arm}:v1",
            issuer_id="human:fixture-approver",
            bounds=bounds,
            minimum_determinism=DeterminismLevel.D3_BYTE_REPLAY_VERIFIED,
            issued_at=issued_at,
            expires_at=f"2026-08-29T00:00:{offset + 12:02d}Z",
            nonce=f"fixture_nonce_{arm}_0001",
        )
        grant_digest = ledger.record_grant(
            grant,
            verifier=signer,
            accepted_at=f"2026-08-29T00:00:{offset + 3:02d}Z",
        )
        output_bytes = canonical_bytes(
            {"cell_id": cell_id, "metric_milli": metric_milli, "synthetic": True}
        )
        output_digest = sha256_bytes(output_bytes)
        receipt = Receipt(
            receipt_id=f"receipt:{arm}:v1",
            matrix_id=plan.matrix_id,
            cell_id=cell_id,
            intent_digest=intent_digest,
            grant_digest=grant_digest,
            grant_id=grant.claims.grant_id,
            executor_id="agent:runtime",
            started_at=f"2026-08-29T00:00:{offset + 4:02d}Z",
            completed_at=f"2026-08-29T00:00:{offset + 5:02d}Z",
            outcome="SUCCEEDED",
            output=ArtifactRef(
                uri=f"rxp+fixture://outputs/{cell_id}.json",
                media_type="application/json",
                sha256=output_digest,
                bytes=len(output_bytes),
            ),
            usage=ResourceUsage(
                gpu_count=0,
                wall_time_seconds=1,
                gpu_time_seconds=0,
                artifact_bytes=len(output_bytes),
            ),
            determinism_level=DeterminismLevel.D3_BYTE_REPLAY_VERIFIED,
            replay_count=2,
            replay_digest=output_digest,
            metadata={"synthetic": True},
        )
        receipt_digest = ledger.record_receipt(
            receipt, replay_registry=replay_registry
        )
        producer_by_type = {
            "code": "agent:experiment-architect",
            "config": "agent:experiment-architect",
            "dataset_manifest": "agent:scout",
            "log": "agent:runtime",
            "metric": "agent:evaluator",
            "trace": "agent:runtime",
            "review": "agent:independent-reviewer",
        }
        non_review_producers = sorted(set(producer_by_type.values()) - {producer_by_type["review"]})
        for evidence_type in (
            "code",
            "config",
            "dataset_manifest",
            "log",
            "metric",
            "trace",
            "review",
        ):
            artifact_bytes = canonical_bytes(
                {"cell_id": cell_id, "evidence_type": evidence_type, "synthetic": True}
            )
            claims = {}
            if evidence_type == "metric":
                claims = {
                    "deterministic": True,
                    "summary_only": False,
                    "raw_data_digest": _digest(f"raw-metrics:{arm}"),
                }
            elif evidence_type == "review":
                claims = {
                    "independent": True,
                    "reviewed_producers": non_review_producers,
                    "reviewer_id": "agent:independent-reviewer",
                    "verdict": "PASS",
                }
            evidence = Evidence(
                evidence_id=f"evidence:{arm}:{evidence_type}:v1",
                matrix_id=plan.matrix_id,
                cell_id=cell_id,
                receipt_digest=receipt_digest,
                evidence_type=evidence_type,
                producer_id=producer_by_type[evidence_type],
                artifact=ArtifactRef(
                    uri=f"rxp+fixture://evidence/{cell_id}/{evidence_type}.json",
                    media_type="application/json",
                    sha256=sha256_bytes(artifact_bytes),
                    bytes=len(artifact_bytes),
                ),
                claims=claims,
                observed_at=f"2026-08-29T00:00:{offset + 6:02d}Z",
            )
            ledger.record_evidence(evidence)
        gate = ledger.assess_evidence(cell_id)
        decision = Decision(
            decision_id=f"decision:{arm}:v1",
            matrix_id=plan.matrix_id,
            cell_id=cell_id,
            intent_digest=intent_digest,
            receipt_digest=receipt_digest,
            evidence_digests=gate.evidence_digests,
            evidence_root=gate.evidence_root,
            gate=gate,
            verdict="KEEP" if arm == "candidate" else "REJECT",
            determinism_level=DeterminismLevel.D3_BYTE_REPLAY_VERIFIED,
            decided_by="agent:research-pi",
            decided_at=f"2026-08-29T00:00:{offset + 7:02d}Z",
            rationale_code="SYNTHETIC_THRESHOLD_RULE",
        )
        ledger.record_decision(decision)
    return ledger


def demo_bytes() -> bytes:
    """Return a byte-identical complete ledger for every invocation."""

    return canonical_bytes(build_demo_ledger().snapshot()) + b"\n"
