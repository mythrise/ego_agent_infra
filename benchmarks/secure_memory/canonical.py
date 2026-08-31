from __future__ import annotations

import hashlib
import json
import math
import re
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_ABSOLUTE_RE = re.compile(r"^[A-Za-z]:[\\/]")


def _json_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _json_value(value.model_dump(mode="json"))
    if isinstance(value, Enum):
        return _json_value(value.value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("canonical JSON forbids NaN and Infinity")
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("canonical JSON object keys must be strings")
            result[key] = _json_value(item)
        return result
    raise TypeError(f"unsupported canonical JSON value: {type(value).__name__}")


def canonical_bytes(value: Any) -> bytes:
    """Encode one value as deterministic, finite UTF-8 JSON."""

    return json.dumps(
        _json_value(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_sha256(domain: str, value: Any) -> str:
    if not domain or not domain.isascii() or "\x00" in domain:
        raise ValueError("canonical digest domain must be non-empty ASCII without NUL")
    prefix = ("egoagentos:" + domain + ":v1\x00").encode("ascii")
    return hashlib.sha256(prefix + canonical_bytes(value)).hexdigest()


def _reject_duplicate_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_non_finite(token: str) -> Any:
    raise ValueError(f"non-finite JSON number: {token}")


def parse_json_bytes(raw: bytes) -> Any:
    """Decode UTF-8 JSON while rejecting duplicate keys and non-finite numbers."""

    if not isinstance(raw, bytes):
        raise TypeError("JSON input must be bytes")
    try:
        text = raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ValueError("JSON input is not valid UTF-8") from exc
    try:
        return json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_non_finite,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc


def validate_sha256_digest(value: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError("expected a lowercase 64-character SHA-256 digest")
    return value


def validate_guest_artifact_path(value: str) -> str:
    """Validate a canonical relative POSIX path used inside a guest artifact."""

    if not isinstance(value, str) or not value:
        raise ValueError("guest artifact path must be a non-empty string")
    if "\x00" in value or "\\" in value or _WINDOWS_ABSOLUTE_RE.match(value):
        raise ValueError("guest artifact path must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("guest artifact path must not be absolute or traverse parents")
    if str(path) != value:
        raise ValueError("guest artifact path must already be canonical")
    return value
