"""Fail-closed authentication for bridge control mutations."""

from __future__ import annotations

import hashlib
import hmac
import os
from typing import Optional

from .errors import BridgeError


MIN_BRIDGE_OPERATOR_KEY_BYTES = 32
MAX_BRIDGE_OPERATOR_KEY_BYTES = 4096


class BridgeOperatorAuthenticator:
    """Validate a bridge-only Bearer key while retaining only its digest."""

    def __init__(
        self,
        key: Optional[str] = None,
        *,
        outbound_ego_operator_key: Optional[str] = None,
    ) -> None:
        configured_key = os.getenv("EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY", "") if key is None else key
        encoded = configured_key.encode("utf-8")
        if encoded and not (
            MIN_BRIDGE_OPERATOR_KEY_BYTES <= len(encoded) <= MAX_BRIDGE_OPERATOR_KEY_BYTES
        ):
            raise ValueError(
                "EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY must contain between %d and %d UTF-8 bytes"
                % (MIN_BRIDGE_OPERATOR_KEY_BYTES, MAX_BRIDGE_OPERATOR_KEY_BYTES)
            )
        outbound_key = (
            os.getenv("EGO_OPERATOR_KEY", "")
            if outbound_ego_operator_key is None
            else outbound_ego_operator_key
        )
        outbound_digest = (
            hashlib.sha256(outbound_key.encode("utf-8")).digest() if outbound_key else None
        )
        if (
            encoded
            and outbound_digest is not None
            and hmac.compare_digest(hashlib.sha256(encoded).digest(), outbound_digest)
        ):
            raise ValueError(
                "EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY must be independent from EGO_OPERATOR_KEY"
            )
        self._key_digest = hashlib.sha256(encoded).digest() if encoded else None

    @property
    def configured(self) -> bool:
        return self._key_digest is not None

    def status(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "scheme": "Bearer",
            "mutations_fail_closed": True,
        }

    def authenticate(self, authorization: Optional[str]) -> None:
        if self._key_digest is None:
            raise BridgeError(
                "bridge_operator_auth_not_configured",
                "Bridge mutations are disabled until EGO_AGENTTEAMS_BRIDGE_OPERATOR_KEY is configured",
                status_code=503,
                retryable=False,
            )
        if not authorization:
            raise BridgeError(
                "bridge_operator_auth_required",
                "A Bearer bridge operator key is required for this mutation",
                status_code=401,
                retryable=False,
            )
        scheme, separator, candidate = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not candidate:
            raise BridgeError(
                "bridge_operator_auth_required",
                "Authorization must use the Bearer bridge-operator-key scheme",
                status_code=401,
                retryable=False,
            )
        candidate_bytes = candidate.encode("utf-8")
        if len(candidate_bytes) > MAX_BRIDGE_OPERATOR_KEY_BYTES:
            candidate_bytes = b"bridge-operator-key-too-long"
        candidate_digest = hashlib.sha256(candidate_bytes).digest()
        if not hmac.compare_digest(candidate_digest, self._key_digest):
            raise BridgeError(
                "bridge_operator_auth_invalid",
                "The supplied bridge operator key is invalid",
                status_code=403,
                retryable=False,
            )
