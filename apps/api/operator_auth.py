"""Fail-closed operator authentication for state-changing API requests."""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from typing import Optional

from .errors import ControlPlaneError


MIN_OPERATOR_KEY_BYTES = 32
MAX_OPERATOR_KEY_BYTES = 4096
DEMO_OPERATOR_ID = "demo.operator"
_OPERATOR_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@/-]{1,119}$")


def _environment_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError("%s must be true or false" % name)


@dataclass(frozen=True)
class OperatorIdentity:
    id: str
    authenticated: bool
    source: str


class OperatorAuthenticator:
    """Validate one deployment-owned Bearer key without retaining it in app state."""

    def __init__(
        self,
        *,
        key: Optional[str] = None,
        operator_id: Optional[str] = None,
        allow_unauthenticated_demo: Optional[bool] = None,
    ) -> None:
        configured_key = os.getenv("EGO_OPERATOR_KEY", "") if key is None else key
        configured_id = (
            os.getenv("EGO_OPERATOR_ID", "operator")
            if operator_id is None
            else operator_id
        )
        if not _OPERATOR_ID.fullmatch(configured_id):
            raise ValueError(
                "EGO_OPERATOR_ID must be 2-120 safe identity characters"
            )

        encoded = configured_key.encode("utf-8")
        if encoded and not MIN_OPERATOR_KEY_BYTES <= len(encoded) <= MAX_OPERATOR_KEY_BYTES:
            raise ValueError(
                "EGO_OPERATOR_KEY must contain between %d and %d UTF-8 bytes"
                % (MIN_OPERATOR_KEY_BYTES, MAX_OPERATOR_KEY_BYTES)
            )
        self._key_digest = hashlib.sha256(encoded).digest() if encoded else None
        self.operator_id = configured_id
        self.allow_unauthenticated_demo = (
            _environment_flag("EGO_ALLOW_UNAUTHENTICATED_DEMO")
            if allow_unauthenticated_demo is None
            else allow_unauthenticated_demo
        )

    @property
    def configured(self) -> bool:
        return self._key_digest is not None

    def status(self) -> dict[str, object]:
        return {
            "configured": self.configured,
            "scheme": "Bearer",
            "operator_id": self.operator_id if self.configured else None,
            "unauthenticated_demo_enabled": self.allow_unauthenticated_demo,
            "live_mutations_fail_closed": True,
        }

    def authenticate(self, authorization: Optional[str]) -> OperatorIdentity:
        if self._key_digest is None:
            raise ControlPlaneError(
                "operator_auth_not_configured",
                "Protected mutations are disabled until EGO_OPERATOR_KEY is configured",
                503,
            )
        if not authorization:
            raise ControlPlaneError(
                "operator_auth_required",
                "A Bearer operator key is required for this mutation",
                401,
            )
        scheme, separator, candidate = authorization.partition(" ")
        if separator != " " or scheme.lower() != "bearer" or not candidate:
            raise ControlPlaneError(
                "operator_auth_required",
                "Authorization must use the Bearer operator-key scheme",
                401,
            )
        candidate_bytes = candidate.encode("utf-8")
        if len(candidate_bytes) > MAX_OPERATOR_KEY_BYTES:
            candidate_bytes = b"operator-key-too-long"
        candidate_digest = hashlib.sha256(candidate_bytes).digest()
        if not hmac.compare_digest(candidate_digest, self._key_digest):
            raise ControlPlaneError(
                "operator_auth_invalid",
                "The supplied operator key is invalid",
                403,
            )
        return OperatorIdentity(
            id=self.operator_id,
            authenticated=True,
            source="configured_operator_key",
        )

    def authorize_demo_or_operator(
        self, authorization: Optional[str]
    ) -> OperatorIdentity:
        if authorization:
            return self.authenticate(authorization)
        if self.allow_unauthenticated_demo:
            return OperatorIdentity(
                id=DEMO_OPERATOR_ID,
                authenticated=False,
                source="explicit_unauthenticated_demo_mode",
            )
        return self.authenticate(authorization)
