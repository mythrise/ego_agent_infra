"""Shared admission gate for every text source entering trusted persistence."""

from __future__ import annotations

import hashlib
import re
from enum import Enum
from typing import Optional, Tuple

from pydantic import Field, field_validator, model_validator

from ..canonical import canonical_sha256, validate_sha256_digest
from ..models import Digest, StrictModel
from .scanner import SCANNER_RULE_VERSION, SCANNER_SHA256, ContentScanner


class DeclaredOrigin(str, Enum):
    MODEL = "MODEL"
    MATRIX = "MATRIX"
    WORKSPACE = "WORKSPACE"
    EVALUATOR = "EVALUATOR"
    MEMORY = "MEMORY"
    BUNDLE = "BUNDLE"


class IngressChannel(str, Enum):
    MODEL_OUTPUT = "MODEL_OUTPUT"
    MATRIX_MESSAGE = "MATRIX_MESSAGE"
    WORKSPACE_OUTPUT = "WORKSPACE_OUTPUT"
    EVALUATOR_OUTPUT = "EVALUATOR_OUTPUT"
    MEMORY_TEXT = "MEMORY_TEXT"
    BUNDLE_TEXT = "BUNDLE_TEXT"


class AdmissionStatus(str, Enum):
    ADMITTED = "ADMITTED"
    QUARANTINED = "QUARANTINED"


class TrustLabel(str, Enum):
    ORIGIN_UNVERIFIED = "ORIGIN_UNVERIFIED"


_EXPECTED_CHANNEL = {
    DeclaredOrigin.MODEL: IngressChannel.MODEL_OUTPUT,
    DeclaredOrigin.MATRIX: IngressChannel.MATRIX_MESSAGE,
    DeclaredOrigin.WORKSPACE: IngressChannel.WORKSPACE_OUTPUT,
    DeclaredOrigin.EVALUATOR: IngressChannel.EVALUATOR_OUTPUT,
    DeclaredOrigin.MEMORY: IngressChannel.MEMORY_TEXT,
    DeclaredOrigin.BUNDLE: IngressChannel.BUNDLE_TEXT,
}
_BOUND_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}$")


class AdmissionReceipt(StrictModel):
    schema_version: str = Field(pattern=r"^secure-memory-admission-receipt/v1$")
    status: AdmissionStatus
    trust_label: TrustLabel
    promotion_authorized: bool
    declared_origin: DeclaredOrigin
    channel: IngressChannel
    campaign_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    generation: int = Field(ge=1)
    sequence: int = Field(ge=1)
    content_sha256: Optional[Digest]
    reason_codes: Tuple[str, ...]
    rule_version: str
    scanner_sha256: Digest
    receipt_sha256: Digest

    @field_validator("reason_codes")
    @classmethod
    def validate_reasons(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("admission reason codes must be sorted and unique")
        return values

    @model_validator(mode="after")
    def validate_receipt(self) -> "AdmissionReceipt":
        if self.trust_label is not TrustLabel.ORIGIN_UNVERIFIED:
            raise ValueError("ingress admission cannot establish a trusted origin")
        if self.promotion_authorized:
            raise ValueError("ingress admission cannot authorize promotion")
        if (self.status is AdmissionStatus.ADMITTED) != (self.content_sha256 is not None):
            raise ValueError("only admitted content may retain its exact digest")
        expected = canonical_sha256(
            "secure-memory-admission-receipt",
            self.model_dump(mode="python", exclude={"receipt_sha256"}),
        )
        if self.receipt_sha256 != expected:
            raise ValueError("admission receipt digest mismatch")
        return self


class AdmissionGate:
    def __init__(self, scanner: Optional[ContentScanner] = None) -> None:
        self.scanner = scanner or ContentScanner()

    def admit(
        self,
        raw: bytes,
        *,
        declared_origin: DeclaredOrigin,
        channel: IngressChannel,
        campaign_id: str,
        task_id: str,
        generation: int,
        sequence: int,
        content_sha256: str,
    ) -> AdmissionReceipt:
        origin = DeclaredOrigin(declared_origin)
        ingress_channel = IngressChannel(channel)
        if not _BOUND_ID.fullmatch(campaign_id) or not _BOUND_ID.fullmatch(task_id):
            raise ValueError("campaign_id and task_id must be bounded stable identifiers")
        if isinstance(generation, bool) or generation < 1:
            raise ValueError("generation must be positive")
        if isinstance(sequence, bool) or sequence < 1:
            raise ValueError("sequence must be positive")
        validate_sha256_digest(content_sha256)

        scan = self.scanner.scan(raw, source_class=ingress_channel.value.casefold())
        reasons = list(scan.reason_codes)
        actual_digest = hashlib.sha256(raw).hexdigest()
        if actual_digest != content_sha256:
            reasons.append("CONTENT_DIGEST_MISMATCH")
        if _EXPECTED_CHANNEL[origin] is not ingress_channel:
            reasons.append("ORIGIN_CHANNEL_MISMATCH")
        reason_codes = tuple(sorted(set(reasons)))
        status = AdmissionStatus.ADMITTED if not reason_codes else AdmissionStatus.QUARANTINED
        core = {
            "schema_version": "secure-memory-admission-receipt/v1",
            "status": status,
            "trust_label": TrustLabel.ORIGIN_UNVERIFIED,
            "promotion_authorized": False,
            "declared_origin": origin,
            "channel": ingress_channel,
            "campaign_id": campaign_id,
            "task_id": task_id,
            "generation": generation,
            "sequence": sequence,
            "content_sha256": actual_digest if status is AdmissionStatus.ADMITTED else None,
            "reason_codes": reason_codes,
            "rule_version": SCANNER_RULE_VERSION,
            "scanner_sha256": SCANNER_SHA256,
        }
        return AdmissionReceipt.model_validate(
            {
                **core,
                "receipt_sha256": canonical_sha256("secure-memory-admission-receipt", core),
            }
        )


__all__ = [
    "AdmissionGate",
    "AdmissionReceipt",
    "AdmissionStatus",
    "DeclaredOrigin",
    "IngressChannel",
    "TrustLabel",
]
