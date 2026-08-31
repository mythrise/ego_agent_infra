"""Signed, scoped, replay-safe evaluator ingress without promotion authority."""

from __future__ import annotations

import hashlib
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from pydantic import Field, model_validator

from ..canonical import canonical_bytes, canonical_sha256, parse_json_bytes
from ..models import Digest, StrictModel
from .admission import (
    AdmissionGate,
    AdmissionReceipt,
    DeclaredOrigin,
    IngressChannel,
    TrustLabel,
)


MAX_EVALUATOR_ENVELOPE_BYTES = 128 * 1024
SignatureVerifier = Callable[[bytes, str, str, str], bool]


class EvaluatorChannelRejected(ValueError):
    pass


class EvaluatorEnvelope(StrictModel):
    schema_version: str = Field(pattern=r"^secure-memory-evaluator-envelope/v1$")
    issuer_id: str = Field(min_length=1, max_length=200)
    key_id: str = Field(min_length=1, max_length=200)
    campaign_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    generation: int = Field(ge=1)
    sequence: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=200)
    payload_sha256: Digest
    payload: Dict[str, Any]
    signature: str = Field(min_length=1, max_length=4096)


class EvaluatorSourceReceipt(StrictModel):
    schema_version: str = Field(pattern=r"^secure-memory-evaluator-source-receipt/v1$")
    issuer_id: str
    key_id: str
    campaign_id: str
    task_id: str
    generation: int
    sequence: int
    idempotency_key: str
    source_verified: bool
    trust_label: TrustLabel
    promotion_authorized: bool
    envelope_sha256: Optional[Digest]
    admission: AdmissionReceipt
    receipt_sha256: Digest

    @model_validator(mode="after")
    def validate_receipt(self) -> "EvaluatorSourceReceipt":
        if not self.source_verified:
            raise ValueError("evaluator source receipts require verified source provenance")
        if self.trust_label is not TrustLabel.ORIGIN_UNVERIFIED or self.promotion_authorized:
            raise ValueError("verified evaluator source receipt cannot authorize promotion")
        expected = canonical_sha256(
            "secure-memory-evaluator-source-receipt",
            self.model_dump(mode="python", exclude={"receipt_sha256"}),
        )
        if self.receipt_sha256 != expected:
            raise ValueError("evaluator source receipt digest mismatch")
        return self


class EvaluatorChannel:
    def __init__(
        self,
        *,
        signature_verifier: SignatureVerifier,
        admission_gate: AdmissionGate,
        expected_issuer_id: str,
        expected_key_id: str,
        campaign_id: str,
        task_id: str,
        generation: int,
    ) -> None:
        if not callable(signature_verifier):
            raise TypeError("signature_verifier must be callable")
        if not all((expected_issuer_id, expected_key_id, campaign_id, task_id)):
            raise ValueError("evaluator channel scope values must be non-empty")
        if isinstance(generation, bool) or generation < 1:
            raise ValueError("evaluator channel generation must be positive")
        self.signature_verifier = signature_verifier
        self.admission_gate = admission_gate
        self.expected_issuer_id = expected_issuer_id
        self.expected_key_id = expected_key_id
        self.campaign_id = campaign_id
        self.task_id = task_id
        self.generation = generation
        self._head_sequence = 0
        self._idempotency: Dict[str, Tuple[Optional[str], EvaluatorSourceReceipt]] = {}
        self._sequences: Dict[int, str] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _decode(frame: bytes) -> EvaluatorEnvelope:
        if not isinstance(frame, bytes):
            raise EvaluatorChannelRejected("envelope_invalid")
        if len(frame) == 0 or len(frame) > MAX_EVALUATOR_ENVELOPE_BYTES:
            raise EvaluatorChannelRejected("envelope_size_invalid")
        try:
            value = parse_json_bytes(frame)
            if not isinstance(value, dict) or canonical_bytes(value) != frame:
                raise ValueError("non-canonical envelope")
            return EvaluatorEnvelope.model_validate(value)
        except (TypeError, ValueError) as exc:
            raise EvaluatorChannelRejected("envelope_invalid") from exc

    def receive(
        self,
        frame: bytes,
        *,
        expected_idempotency_key: str,
    ) -> EvaluatorSourceReceipt:
        envelope = self._decode(frame)
        if envelope.issuer_id != self.expected_issuer_id:
            raise EvaluatorChannelRejected("issuer_mismatch")
        if envelope.key_id != self.expected_key_id:
            raise EvaluatorChannelRejected("key_mismatch")
        if envelope.campaign_id != self.campaign_id:
            raise EvaluatorChannelRejected("campaign_mismatch")
        if envelope.task_id != self.task_id:
            raise EvaluatorChannelRejected("task_mismatch")
        if envelope.generation != self.generation:
            raise EvaluatorChannelRejected("generation_mismatch")
        if not expected_idempotency_key or envelope.idempotency_key != expected_idempotency_key:
            raise EvaluatorChannelRejected("idempotency_mismatch")

        payload_bytes = canonical_bytes(envelope.payload)
        if hashlib.sha256(payload_bytes).hexdigest() != envelope.payload_sha256:
            raise EvaluatorChannelRejected("payload_digest_mismatch")
        signed_core = envelope.model_dump(mode="python", exclude={"signature"})
        signed_bytes = canonical_bytes(signed_core)
        try:
            signature_valid = self.signature_verifier(
                signed_bytes,
                envelope.signature,
                envelope.issuer_id,
                envelope.key_id,
            )
        except Exception as exc:
            raise EvaluatorChannelRejected("signature_invalid") from exc
        if signature_valid is not True:
            raise EvaluatorChannelRejected("signature_invalid")

        frame_sha256 = hashlib.sha256(frame).hexdigest()
        with self._lock:
            prior = self._idempotency.get(envelope.idempotency_key)
            if prior is not None:
                prior_digest, prior_receipt = prior
                if prior_digest is not None and prior_digest == frame_sha256:
                    return prior_receipt
                raise EvaluatorChannelRejected("idempotency_conflict")
            prior_sequence = self._sequences.get(envelope.sequence)
            if prior_sequence is not None:
                raise EvaluatorChannelRejected("sequence_reuse")
            if envelope.sequence != self._head_sequence + 1:
                raise EvaluatorChannelRejected("sequence_out_of_order")

            admission = self.admission_gate.admit(
                payload_bytes,
                declared_origin=DeclaredOrigin.EVALUATOR,
                channel=IngressChannel.EVALUATOR_OUTPUT,
                campaign_id=envelope.campaign_id,
                task_id=envelope.task_id,
                generation=envelope.generation,
                sequence=envelope.sequence,
                content_sha256=envelope.payload_sha256,
            )
            admitted = admission.content_sha256 is not None
            core = {
                "schema_version": "secure-memory-evaluator-source-receipt/v1",
                "issuer_id": envelope.issuer_id,
                "key_id": envelope.key_id,
                "campaign_id": envelope.campaign_id,
                "task_id": envelope.task_id,
                "generation": envelope.generation,
                "sequence": envelope.sequence,
                "idempotency_key": envelope.idempotency_key,
                "source_verified": True,
                "trust_label": TrustLabel.ORIGIN_UNVERIFIED,
                "promotion_authorized": False,
                "envelope_sha256": frame_sha256 if admitted else None,
                "admission": admission,
            }
            receipt = EvaluatorSourceReceipt.model_validate(
                {
                    **core,
                    "receipt_sha256": canonical_sha256(
                        "secure-memory-evaluator-source-receipt", core
                    ),
                }
            )
            retained_digest = frame_sha256 if admitted else None
            self._idempotency[envelope.idempotency_key] = (retained_digest, receipt)
            self._sequences[envelope.sequence] = receipt.receipt_sha256
            self._head_sequence = envelope.sequence
            return receipt


__all__ = [
    "EvaluatorChannel",
    "EvaluatorChannelRejected",
    "EvaluatorEnvelope",
    "EvaluatorSourceReceipt",
    "MAX_EVALUATOR_ENVELOPE_BYTES",
    "SignatureVerifier",
]
