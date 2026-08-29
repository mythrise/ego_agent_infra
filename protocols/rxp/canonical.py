"""Canonical serialization and domain-separated hashing for RXP/1.

RXP deliberately rejects binary floating point. Experiment parameters and metrics
that require fractions must use scaled integers or decimal strings. This keeps the
wire digest independent of a language runtime's float formatting choices.
"""

from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from collections.abc import Mapping, Sequence
from enum import Enum
from typing import Any

from pydantic import BaseModel

DOCUMENT_DOMAIN = b"RXP/1/document\x00"
LEDGER_ENTRY_DOMAIN = b"RXP/1/ledger-entry\x00"
LEDGER_ROOT_DOMAIN = b"RXP/1/ledger-root\x00"
MERKLE_LEAF_DOMAIN = b"RXP/1/merkle-leaf\x00"
MERKLE_NODE_DOMAIN = b"RXP/1/merkle-node\x00"
MERKLE_EMPTY_ROOT = "sha256:" + hashlib.sha256(b"RXP/1/merkle/empty").hexdigest()
GENESIS_ROOT = "sha256:" + hashlib.sha256(b"RXP/1/ledger/genesis").hexdigest()


def _normalise_string(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def _normalise(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _normalise(
            value.model_dump(mode="json", by_alias=True, exclude_none=False)
        )
    if isinstance(value, Enum):
        return _normalise(value.value)
    if isinstance(value, Mapping):
        normalised: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("RXP canonical object keys must be strings")
            canonical_key = _normalise_string(key)
            if canonical_key in normalised:
                raise ValueError("RXP canonical object contains NFC-colliding keys")
            normalised[canonical_key] = _normalise(item)
        return normalised
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_normalise(item) for item in value]
    if isinstance(value, str):
        return _normalise_string(value)
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        if not -(2**63) <= value < 2**63:
            raise ValueError("RXP canonical integers must fit signed 64-bit range")
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers cannot be canonically encoded")
        raise TypeError("RXP canonical form forbids binary floating point")
    raise TypeError(f"unsupported RXP canonical value: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    """Return the normative RXP-CJ/1 UTF-8 byte representation."""

    return json.dumps(
        _normalise(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def canonical_json(value: Any) -> str:
    return canonical_bytes(value).decode("utf-8")


def _digest(domain: bytes, payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(domain + payload).hexdigest()


def digest_document(value: Any) -> str:
    """Digest one complete RXP document with domain separation."""

    return _digest(DOCUMENT_DOMAIN, canonical_bytes(value))


def digest_ledger_entry(value: Any) -> str:
    return _digest(LEDGER_ENTRY_DOMAIN, canonical_bytes(value))


def sha256_bytes(value: bytes) -> str:
    """Digest opaque artifact bytes (no RXP document domain prefix)."""

    return "sha256:" + hashlib.sha256(value).hexdigest()


def extend_ledger_root(previous_root: str, entry_digest: str) -> str:
    """Extend an append-only ledger root with a committed entry digest."""

    previous = digest_hex(previous_root)
    entry = digest_hex(entry_digest)
    return _digest(LEDGER_ROOT_DOMAIN, previous + entry)


def merkle_root(digests: Sequence[str]) -> str:
    """Commit to an unordered digest set using sorted leaves and duplicate-last nodes."""

    if not digests:
        return MERKLE_EMPTY_ROOT
    if len(digests) != len(set(digests)):
        raise ValueError("RXP Merkle sets cannot contain duplicate digests")
    level = [digest_hex(_digest(MERKLE_LEAF_DOMAIN, digest_hex(item))) for item in sorted(digests)]
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            digest_hex(_digest(MERKLE_NODE_DOMAIN, level[index] + level[index + 1]))
            for index in range(0, len(level), 2)
        ]
    return "sha256:" + level[0].hex()


def digest_hex(value: str) -> bytes:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ValueError("expected sha256:<64 lowercase hex> digest")
    encoded = value.removeprefix("sha256:")
    if len(encoded) != 64 or any(character not in "0123456789abcdef" for character in encoded):
        raise ValueError("expected sha256:<64 lowercase hex> digest")
    return bytes.fromhex(encoded)
