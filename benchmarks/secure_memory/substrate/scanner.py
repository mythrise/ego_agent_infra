"""Deterministic fail-closed scanning for text entering trusted persistence."""

from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any, Iterable, Optional, Tuple

from pydantic import Field, field_validator

from ..canonical import canonical_bytes, canonical_sha256, parse_json_bytes
from ..models import Digest, StrictModel


MAX_INGRESS_BYTES = 64 * 1024
SCANNER_RULE_VERSION = "secure-memory-ingress-scanner/1"
INGRESS_TEXT_SCHEMA_VERSION = "secure-memory-ingress-text/v1"

_POLICY_IDS = (
    "AUTHORITY_FIELD",
    "CONTROL_CHARACTER",
    "CREDENTIAL_PATTERN",
    "JSON_SHAPE_INVALID",
    "NON_CANONICAL_JSON",
    "NUL_BYTE",
    "PROMPT_AUTHORITY_CLAIM",
    "SIZE_INVALID",
    "UTF8_INVALID",
)

_AUTHORITY_PARTS = frozenset(
    {
        "approval",
        "authority",
        "capability",
        "decision",
        "evaluator",
        "gate",
        "issuer",
        "key",
        "origin",
        "promotion",
        "signature",
        "trusted",
        "validated",
    }
)

_CREDENTIAL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bauthorization\s*:\s*(?:bearer|basic)\s+\S+",
        r"\bbearer\s+[A-Za-z0-9._~+/=-]{12,}",
        r"\b(?:api[_-]?key|access[_-]?token|password|passwd|hmac[_-]?(?:key|secret))\s*[:=]\s*\S{8,}",
        r"\b(?:password|passwd)\s+is\s+(?!(?:not|never|required|optional|redacted|omitted|hidden)\b)\S{4,}",
        r"\bsk-[A-Za-z0-9_-]{20,}",
        r"\bAKIA[A-Z0-9]{16}\b",
        r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----",
        r"\b(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s:/]+:[^\s@/]+@",
        r"\bcredential[-_ ]canary[-_A-Za-z0-9]{6,}",
    )
)

_PROMPT_AUTHORITY_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in (
        r"\bignore\s+(?:all\s+)?previous\s+instructions?\b.*\b(?:trusted|authorized|approve|authority)\b",
        r"\bsystem\s+override\b.*\b(?:authorized|approve|trusted|permission)\b",
        r"\bdo\s+not\s+scan\b.*\b(?:evaluator|decision|pass|approve)\b",
        r"\b(?:treat|mark)\s+(?:this|me|content|message)\s+as\s+(?:trusted|validated|approved)\b",
        r"\bi\s+(?:am|have\s+been)\s+authorized\s+to\s+(?:approve|grant|override)\b",
        r"\b(?:i|we|this\s+(?:message|content))\s+(?:am|are|is|have\s+been|has\s+been)\s+(?:approved|authorized)\s+to\s+(?:mark|promote|store|treat|write)\b.*\b(?:trusted|validated|approved|authority)\b",
        r"\b(?:this\s+)?approved\s+(?:decision|review|message|content)\b.*\b(?:grants?|confers?|provides?)\s+(?:approval\s+)?authority\b.*\b(?:mark|promote|store|treat|write)\b.*\btrusted\b",
        r"\bbypass\s+(?:the\s+)?(?:scanner|admission|policy)\b",
        r"\b(?:developer|system|reviewer|evaluator)\s+(?:grant|permission|approval)\b.*\b(?:authority|authorized|bypass|promote|trusted)\b",
        r"\b(?:approved|authorized|permission|authority)\b.*\bpromot(?:e|ion)\b.*\btrusted\s+memory\b",
        r"\bpromote\b.*\btrusted\s+memory\b.*\b(?:approved|authorized|permission|authority)\b",
        r"\b(?:this\s+)?(?:content|message|memory|result)\s+(?:is|was|has\s+been)\s+(?:approved|authorized)(?:\s+and)?\s+(?:trusted|validated)\b",
    )
)

SCANNER_RULE_MANIFEST = {
    "rule_version": SCANNER_RULE_VERSION,
    "text_schema_version": INGRESS_TEXT_SCHEMA_VERSION,
    "max_ingress_bytes": MAX_INGRESS_BYTES,
    "policy_ids": _POLICY_IDS,
    "authority_parts": tuple(sorted(_AUTHORITY_PARTS)),
    "credential_patterns": tuple(
        (pattern.pattern, pattern.flags) for pattern in _CREDENTIAL_PATTERNS
    ),
    "prompt_authority_patterns": tuple(
        (pattern.pattern, pattern.flags) for pattern in _PROMPT_AUTHORITY_PATTERNS
    ),
    "control_categories": ("Cc", "Cf"),
    "accepted_shape": ("schema_version", "text"),
    "accepted_text_min_chars": 1,
    "canonical_json_required": True,
    "strict_utf8_required": True,
}
SCANNER_SHA256 = canonical_sha256(
    "secure-memory-scanner-rules",
    SCANNER_RULE_MANIFEST,
)


class ScanReceipt(StrictModel):
    """Bounded scanner outcome. It never contains input text."""

    schema_version: str = Field(pattern=r"^secure-memory-scan-receipt/v1$")
    admitted: bool
    source_class: str = Field(min_length=1, max_length=120)
    reason_codes: Tuple[str, ...]
    finding_count: int = Field(ge=0)
    admitted_content_sha256: Optional[Digest]
    rule_version: str = Field(pattern=r"^secure-memory-ingress-scanner/1$")
    scanner_sha256: Digest

    @field_validator("reason_codes")
    @classmethod
    def validate_reasons(cls, values: Tuple[str, ...]) -> Tuple[str, ...]:
        if values != tuple(sorted(set(values))):
            raise ValueError("scanner reason codes must be sorted and unique")
        return values


def _receipt(
    *,
    admitted: bool,
    source_class: str,
    reasons: Iterable[str],
    accepted_digest: Optional[str],
) -> ScanReceipt:
    reason_codes = tuple(sorted(set(reasons)))
    return ScanReceipt(
        schema_version="secure-memory-scan-receipt/v1",
        admitted=admitted,
        source_class=source_class,
        reason_codes=reason_codes,
        finding_count=len(reason_codes),
        admitted_content_sha256=accepted_digest if admitted else None,
        rule_version=SCANNER_RULE_VERSION,
        scanner_sha256=SCANNER_SHA256,
    )


def _strings(value: Any) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, nested in value.items():
            yield str(key)
            yield from _strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _strings(nested)


def _has_authority_key(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    for key, nested in value.items():
        normalized = re.sub(r"[^a-z0-9]+", "_", str(key).casefold()).strip("_")
        parts = set(normalized.split("_"))
        if parts.intersection(_AUTHORITY_PARTS) or _has_authority_key(nested):
            return True
        if isinstance(nested, list) and any(_has_authority_key(item) for item in nested):
            return True
    return False


class ContentScanner:
    """Scan one exact canonical JSON text record without retaining its content."""

    def scan(self, raw: bytes, *, source_class: str) -> ScanReceipt:
        if not isinstance(source_class, str) or not source_class or len(source_class) > 120:
            raise ValueError("source_class must be a bounded non-empty string")
        if not isinstance(raw, bytes):
            raise TypeError("scanner input must be bytes")

        reasons = []
        if len(raw) == 0 or len(raw) > MAX_INGRESS_BYTES:
            reasons.append("SIZE_INVALID")
            return _receipt(
                admitted=False,
                source_class=source_class,
                reasons=reasons,
                accepted_digest=None,
            )
        if b"\x00" in raw:
            reasons.append("NUL_BYTE")
        try:
            raw.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            reasons.append("UTF8_INVALID")
            return _receipt(
                admitted=False,
                source_class=source_class,
                reasons=reasons,
                accepted_digest=None,
            )

        try:
            value = parse_json_bytes(raw)
        except (TypeError, ValueError):
            reasons.append("JSON_SHAPE_INVALID")
            return _receipt(
                admitted=False,
                source_class=source_class,
                reasons=reasons,
                accepted_digest=None,
            )
        if canonical_bytes(value) != raw:
            reasons.append("NON_CANONICAL_JSON")
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "text"}
            or value.get("schema_version") != INGRESS_TEXT_SCHEMA_VERSION
            or not isinstance(value.get("text"), str)
            or not value.get("text")
        ):
            reasons.append("JSON_SHAPE_INVALID")
        if _has_authority_key(value):
            reasons.append("AUTHORITY_FIELD")

        for text in _strings(value):
            if any(
                character == "\x00" or unicodedata.category(character) in {"Cc", "Cf"}
                for character in text
            ):
                reasons.append("NUL_BYTE" if "\x00" in text else "CONTROL_CHARACTER")
            if any(pattern.search(text) for pattern in _CREDENTIAL_PATTERNS):
                reasons.append("CREDENTIAL_PATTERN")
            if any(pattern.search(text) for pattern in _PROMPT_AUTHORITY_PATTERNS):
                reasons.append("PROMPT_AUTHORITY_CLAIM")

        admitted = not reasons
        return _receipt(
            admitted=admitted,
            source_class=source_class,
            reasons=reasons,
            accepted_digest=hashlib.sha256(raw).hexdigest() if admitted else None,
        )


__all__ = [
    "ContentScanner",
    "INGRESS_TEXT_SCHEMA_VERSION",
    "MAX_INGRESS_BYTES",
    "SCANNER_RULE_VERSION",
    "SCANNER_RULE_MANIFEST",
    "SCANNER_SHA256",
    "ScanReceipt",
]
