"""Scoped, expiring, single-use RXP grant profile."""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
import threading
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from .canonical import canonical_bytes, digest_document
from .errors import RXPError
from .models import (
    DeterminismLevel,
    Grant,
    GrantClaims,
    Intent,
    LegacyApprovalV1Binding,
    ResourceBounds,
)

GRANT_SIGNATURE_DOMAIN = b"RXP/1/grant-signature\x00"
MAX_GRANT_TTL_SECONDS = 3_600
CLOCK_SKEW_SECONDS = 30


def parse_utc(value: str) -> datetime:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as exc:
        raise RXPError("timestamp_invalid", "Expected an RFC 3339 UTC second timestamp") from exc
    return parsed


class ReplayRegistry(Protocol):
    def consume(self, namespace: str, identifier: str) -> bool:
        """Atomically return true exactly once per namespace and identifier."""


class InMemoryReplayRegistry:
    """Thread-safe reference registry; deployments should use durable atomic storage."""

    def __init__(self) -> None:
        self._seen: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    def consume(self, namespace: str, identifier: str) -> bool:
        key = (namespace, identifier)
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            return True


class SQLiteReplayRegistry:
    """Durable cross-process grant consumption using a SQLite uniqueness constraint."""

    def __init__(self, path: str | Path, *, timeout_seconds: int = 30) -> None:
        if not 1 <= timeout_seconds <= 300:
            raise RXPError("replay_timeout_invalid", "Replay timeout must be 1–300 seconds")
        supplied = Path(path).expanduser()
        if supplied.exists() and supplied.is_symlink():
            raise RXPError("replay_registry_symlink", "Replay registry must not be a symlink")
        supplied.parent.mkdir(parents=True, exist_ok=True)
        self.path = supplied.resolve()
        self.timeout_seconds = timeout_seconds
        try:
            with self._connect() as connection:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """
                    CREATE TABLE IF NOT EXISTS consumed_grants (
                        namespace TEXT NOT NULL,
                        identifier TEXT NOT NULL,
                        PRIMARY KEY (namespace, identifier)
                    )
                    """
                )
        except sqlite3.Error as exc:
            raise RXPError(
                "replay_registry_unavailable", "Could not initialize replay registry"
            ) from exc

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=self.timeout_seconds)

    def consume(self, namespace: str, identifier: str) -> bool:
        if not namespace or not identifier or len(namespace) > 128 or len(identifier) > 256:
            raise RXPError("replay_key_invalid", "Replay registry key is invalid")
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    "INSERT OR IGNORE INTO consumed_grants(namespace, identifier) VALUES (?, ?)",
                    (namespace, identifier),
                )
                return cursor.rowcount == 1
        except sqlite3.Error as exc:
            raise RXPError(
                "replay_registry_unavailable", "Could not update replay registry"
            ) from exc


class GrantSigner:
    """Reference HMAC-SHA256 grant issuer/verifier.

    HMAC is the required local profile, not a claim that every RXP deployment shares
    one secret. A deployment may add an asymmetric profile under a future algorithm
    identifier while retaining the same GrantClaims and canonical bytes.
    """

    def __init__(self, secret: bytes, *, key_id: str) -> None:
        if len(secret) < 32:
            raise RXPError("grant_key_too_short", "RXP HMAC keys must be at least 32 bytes")
        self._secret = bytes(secret)
        self.key_id = key_id

    def _signature(self, claims: GrantClaims) -> str:
        protected = {
            "algorithm": "HMAC-SHA256",
            "claims": claims,
            "key_id": self.key_id,
        }
        signature = hmac.new(
            self._secret,
            GRANT_SIGNATURE_DOMAIN + canonical_bytes(protected),
            hashlib.sha256,
        ).hexdigest()
        return "hmac-sha256:" + signature

    def issue(
        self,
        intent: Intent,
        *,
        grant_id: str,
        issuer_id: str,
        bounds: ResourceBounds,
        minimum_determinism: DeterminismLevel,
        issued_at: str,
        expires_at: str,
        nonce: str,
        legacy_approval_v1: LegacyApprovalV1Binding | None = None,
    ) -> Grant:
        if not bounds.contains(intent.requested_resources):
            raise RXPError("grant_bounds_too_narrow", "Grant bounds do not cover the intent")
        if minimum_determinism.rank < intent.required_determinism.rank:
            raise RXPError(
                "grant_determinism_too_weak",
                "Grant minimum determinism is below the intent requirement",
            )
        self._validate_window(issued_at, expires_at)
        if legacy_approval_v1 != intent.approval_v1_binding:
            raise RXPError(
                "legacy_binding_mismatch", "Grant legacy binding must exactly match the intent"
            )
        claims = GrantClaims(
            grant_id=grant_id,
            issuer_id=issuer_id,
            intent_id=intent.intent_id,
            intent_digest=digest_document(intent),
            matrix_id=intent.matrix_id,
            cell_id=intent.cell_id,
            action=intent.action,
            scope=intent.scope,
            action_payload_digest=intent.action_payload_digest,
            bounds=bounds,
            minimum_determinism=minimum_determinism,
            issued_at=issued_at,
            expires_at=expires_at,
            nonce=nonce,
            legacy_approval_v1=legacy_approval_v1,
        )
        return Grant(key_id=self.key_id, claims=claims, signature=self._signature(claims))

    def verify(self, grant: Grant, intent: Intent, *, checked_at: str) -> None:
        if grant.key_id != self.key_id:
            raise RXPError("grant_key_unknown", "The grant key id is not trusted")
        if not hmac.compare_digest(grant.signature, self._signature(grant.claims)):
            raise RXPError("grant_signature_invalid", "The grant signature is invalid")
        claims = grant.claims
        expected = {
            "intent_id": intent.intent_id,
            "intent_digest": digest_document(intent),
            "matrix_id": intent.matrix_id,
            "cell_id": intent.cell_id,
            "action": intent.action,
            "scope": intent.scope,
            "action_payload_digest": intent.action_payload_digest,
        }
        mismatches = [name for name, value in expected.items() if getattr(claims, name) != value]
        if mismatches:
            raise RXPError(
                "grant_scope_mismatch",
                "Grant is not valid for this exact matrix cell and intent",
                {"mismatched_fields": sorted(mismatches)},
            )
        if claims.legacy_approval_v1 != intent.approval_v1_binding:
            raise RXPError("legacy_binding_mismatch", "Grant legacy binding does not match intent")
        if not claims.bounds.contains(intent.requested_resources):
            raise RXPError("grant_bounds_exceeded", "Intent exceeds the signed resource bounds")
        if claims.minimum_determinism.rank < intent.required_determinism.rank:
            raise RXPError(
                "grant_determinism_too_weak",
                "Signed minimum determinism is below the intent requirement",
            )
        self._validate_window(claims.issued_at, claims.expires_at)
        checked = parse_utc(checked_at)
        issued = parse_utc(claims.issued_at)
        expires = parse_utc(claims.expires_at)
        if issued.timestamp() > checked.timestamp() + CLOCK_SKEW_SECONDS:
            raise RXPError("grant_not_yet_valid", "The grant was issued in the future")
        if checked >= expires:
            raise RXPError(
                "grant_expired", "The grant has expired", {"expires_at": claims.expires_at}
            )

    @staticmethod
    def _validate_window(issued_at: str, expires_at: str) -> None:
        issued = parse_utc(issued_at)
        expires = parse_utc(expires_at)
        ttl = int((expires - issued).total_seconds())
        if not 1 <= ttl <= MAX_GRANT_TTL_SECONDS:
            raise RXPError(
                "grant_ttl_invalid",
                f"Grant TTL must be between 1 and {MAX_GRANT_TTL_SECONDS} seconds",
            )


def migrate_consumed_approval_v1(
    intent: Intent,
    legacy_claims: Mapping[str, Any],
    *,
    legacy_token_sha256: str,
    signer: GrantSigner,
    migration_registry: ReplayRegistry,
    grant_id: str,
    issuer_id: str,
    bounds: ResourceBounds,
    minimum_determinism: DeterminismLevel,
    nonce: str,
) -> Grant:
    """Mint one RXP grant from an already validated-and-consumed egoap1 token.

    This function intentionally does not parse or validate the legacy token. The caller
    must first use approval-token-v1's validator, which atomically consumes its JTI.
    The separate migration registry prevents one consumed claim object from being
    converted more than once. No raw token is embedded in RXP.
    """

    required = {
        "jti",
        "action",
        "scope",
        "action_digest",
        "config_sha256",
        "issued_at",
        "expires_at",
    }
    missing = sorted(required - set(legacy_claims))
    if missing:
        raise RXPError("legacy_claims_invalid", "Legacy claims are incomplete", {"missing": missing})
    binding_data = {
        "jti": legacy_claims["jti"],
        "action_digest": legacy_claims["action_digest"],
        "config_sha256": legacy_claims["config_sha256"],
        "token_sha256": legacy_token_sha256,
    }
    try:
        binding = LegacyApprovalV1Binding.model_validate(binding_data)
    except ValidationError as exc:
        raise RXPError("legacy_claims_invalid", "Legacy claims are malformed") from exc
    expected_binding = intent.approval_v1_binding
    if expected_binding is None or binding != expected_binding:
        raise RXPError(
            "legacy_binding_mismatch", "Legacy claims do not match the frozen intent binding"
        )
    if legacy_claims["action"] != intent.action or legacy_claims["scope"] != intent.scope:
        raise RXPError("legacy_scope_mismatch", "Legacy action or scope does not match intent")
    if binding.config_sha256 != intent.run_manifest.config_sha256.removeprefix("sha256:"):
        raise RXPError("legacy_config_mismatch", "Legacy config digest does not match manifest")
    grant = signer.issue(
        intent,
        grant_id=grant_id,
        issuer_id=issuer_id,
        bounds=bounds,
        minimum_determinism=minimum_determinism,
        issued_at=_legacy_epoch_to_utc(legacy_claims["issued_at"]),
        expires_at=_legacy_epoch_to_utc(legacy_claims["expires_at"]),
        nonce=nonce,
        legacy_approval_v1=binding,
    )
    if not migration_registry.consume("approval-token-v1-migration", binding.jti):
        raise RXPError("legacy_approval_replayed", "Legacy approval was already migrated")
    return grant


def _legacy_epoch_to_utc(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RXPError("legacy_claims_invalid", "Legacy epoch timestamps must be integers")
    try:
        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError) as exc:
        raise RXPError("legacy_claims_invalid", "Legacy epoch timestamp is out of range") from exc
