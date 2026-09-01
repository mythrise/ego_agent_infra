"""Authenticated project-level access to current eligible trusted-memory facts."""

from __future__ import annotations

import base64
import hmac
from typing import Any, Mapping, Sequence

from fastapi import FastAPI, Header, Request

from benchmarks.secure_memory.canonical import canonical_bytes, canonical_sha256

from ..errors import ControlPlaneError
from .focus_contracts import (
    FocusEvidenceRef,
    FocusMemoryQuery,
    TrustedFocusFact,
    TrustedMemoryFocusSource,
    build_trusted_memory_focus_source,
)
from .models import MemoryOrigin, MemoryState, TrustedFact


FOCUS_MEMORY_PATH = "/api/v1/internal/trusted-memory/focus"


def validate_focus_service_token(token: str) -> str:
    """Validate configuration without ever returning or logging token material."""

    if token and len(token.encode("utf-8")) < 32:
        raise ValueError("EGO_TRUSTED_MEMORY_SERVICE_TOKEN must contain at least 32 bytes")
    return token


def _row_value(row: Mapping[str, Any], key: str) -> Any:
    try:
        return row[key]
    except KeyError as error:  # pragma: no cover - database contract guard
        raise ValueError("trusted-memory projection row is missing %s" % key) from error


class TrustedMemoryFocusService:
    """Read-only adapter over the authoritative current trusted-memory projection."""

    def __init__(self, store: Any, *, tenant_id: str) -> None:
        if not tenant_id or len(tenant_id) > 200:
            raise ValueError("trusted-memory focus tenant_id must contain 1-200 characters")
        self.store = store
        self.tenant_id = tenant_id

    def _scan_current(self, query: FocusMemoryQuery) -> tuple[Sequence[Mapping[str, Any]], bool]:
        if query.tenant_id != self.tenant_id:
            raise ControlPlaneError(
                "trusted_memory_tenant_forbidden",
                "Trusted-memory focus queries cannot cross the configured tenant boundary",
                403,
            )
        store_tenant = getattr(self.store, "tenant_id", self.tenant_id)
        if store_tenant != self.tenant_id:
            raise ControlPlaneError(
                "trusted_memory_store_tenant_mismatch",
                "Trusted-memory store and focus service tenants do not match",
                500,
            )

        connection = self.store._connect()
        try:
            requested = query.scan_limit + 1
            if self.store.engine == "postgresql":
                rows = connection.execute(
                    """
                    SELECT tenant_id, project_id, lineage_id, revision_id, revision,
                           fact_digest, state, eligible, fact_bytes, fact_event_hash,
                           projection_event_hash
                      FROM trusted_memory_current
                     WHERE tenant_id=%s AND project_id=%s AND eligible IS TRUE
                     ORDER BY lineage_id
                     LIMIT %s
                    """,
                    (query.tenant_id, query.project_id, requested),
                ).fetchall()
            elif self.store.engine == "sqlite":
                rows = connection.execute(
                    """
                    SELECT tenant_id, project_id, lineage_id, revision_id, revision,
                           fact_digest, state, eligible, fact_bytes, fact_event_hash,
                           projection_event_hash
                      FROM trusted_memory_current
                     WHERE tenant_id=? AND project_id=? AND eligible=1
                     ORDER BY lineage_id
                     LIMIT ?
                    """,
                    (query.tenant_id, query.project_id, requested),
                ).fetchall()
            else:  # pragma: no cover - unsupported store guard
                raise ControlPlaneError(
                    "trusted_memory_store_unsupported",
                    "Trusted-memory focus requires the SQLite or PostgreSQL store",
                    500,
                    {"engine": str(getattr(self.store, "engine", "unknown"))},
                )
        finally:
            self.store._close(connection)
        truncated = len(rows) > query.scan_limit
        return rows[: query.scan_limit], truncated

    @staticmethod
    def _validate_projection_row(row: Mapping[str, Any], fact: TrustedFact) -> None:
        expected = {
            "tenant_id": fact.scope.tenant_id,
            "project_id": fact.scope.project_id,
            "lineage_id": fact.lineage_id,
            "revision_id": fact.revision_id,
            "revision": fact.revision,
            "fact_digest": fact.trusted_fact_digest,
            "state": fact.state.value,
        }
        mismatched = tuple(
            key for key, value in expected.items() if _row_value(row, key) != value
        )
        if mismatched:
            raise ValueError(
                "trusted-memory current projection does not match canonical fact: %s"
                % ", ".join(mismatched)
            )
        eligible = bool(_row_value(row, "eligible"))
        if not eligible:
            raise ValueError("trusted-memory focus cannot consume an ineligible projection")

    @staticmethod
    def _focus_fact(row: Mapping[str, Any], fact: TrustedFact) -> TrustedFocusFact:
        statement = base64.b64decode(
            fact.core.statement_utf8_base64,
            validate=True,
        ).decode("utf-8")
        evidence = tuple(
            sorted(
                (
                    FocusEvidenceRef(evidence_id=evidence_id, evidence_digest=evidence_digest)
                    for evidence_id, evidence_digest in zip(
                        fact.provenance.evidence_ids,
                        fact.provenance.evidence_digests,
                    )
                ),
                key=canonical_bytes,
            )
        )
        return TrustedFocusFact(
            fact_sha256=fact.trusted_fact_digest,
            tenant_id=fact.scope.tenant_id,
            project_id=fact.scope.project_id,
            lineage_id=fact.lineage_id,
            revision_id=fact.revision_id,
            revision=fact.revision,
            fact_kind=fact.core.fact_kind,
            statement=statement,
            component=fact.scope.component,
            version=fact.scope.version,
            outcome=fact.outcome,
            origin=fact.origin,
            evidence=evidence,
            closure_digest=fact.provenance.decision_closure_digest,
            provenance_sha256=canonical_sha256(
                "trusted-memory-fact-provenance", fact.provenance
            ),
            projection_event_hash=str(_row_value(row, "projection_event_hash")),
        )

    def fetch(self, query: FocusMemoryQuery) -> TrustedMemoryFocusSource:
        rows, truncated_by_scan_limit = self._scan_current(query)
        facts = []
        for row in rows:
            lineage_id = str(_row_value(row, "lineage_id"))
            try:
                fact = TrustedFact.model_validate_json(bytes(_row_value(row, "fact_bytes")))
                self._validate_projection_row(row, fact)
                if fact.state is not MemoryState.VALIDATED:
                    raise ValueError("focus source requires VALIDATED facts")
                if fact.origin not in {
                    MemoryOrigin.ATTESTED_EXTERNAL,
                    MemoryOrigin.LOCAL_TRUSTED,
                }:
                    raise ValueError("focus source requires a trusted origin")
                if fact.scope.tenant_id != query.tenant_id:
                    raise ValueError("focus fact tenant does not match query")
                if fact.scope.project_id != query.project_id:
                    raise ValueError("focus fact project does not match query")
                if fact.outcome not in query.outcomes or fact.origin not in query.origins:
                    continue
                facts.append(self._focus_fact(row, fact))
            except ControlPlaneError:
                raise
            except (TypeError, ValueError, UnicodeDecodeError) as error:
                raise ControlPlaneError(
                    "trusted_memory_projection_invalid",
                    "Current trusted-memory projection failed canonical validation",
                    500,
                    {"lineage_id": lineage_id, "error_type": type(error).__name__},
                ) from error

        return build_trusted_memory_focus_source(
            query,
            facts,
            scanned_count=len(rows),
            matching_count=len(facts),
            truncated_by_scan_limit=truncated_by_scan_limit,
        )


def _authorize(request: Request, authorization: str) -> None:
    expected = str(getattr(request.app.state, "trusted_memory_service_token", ""))
    if not expected:
        raise ControlPlaneError(
            "trusted_memory_service_not_configured",
            "The authenticated trusted-memory focus service is not configured",
            503,
        )
    prefix = "Bearer "
    if not authorization.startswith(prefix):
        raise ControlPlaneError(
            "trusted_memory_service_unauthorized",
            "A valid Bearer service token is required",
            401,
        )
    supplied = authorization[len(prefix) :]
    if not supplied or not hmac.compare_digest(supplied, expected):
        raise ControlPlaneError(
            "trusted_memory_service_unauthorized",
            "A valid Bearer service token is required",
            401,
        )


def register_trusted_memory_focus_routes(application: FastAPI) -> None:
    """Register the machine-to-machine focus endpoint on an existing API app."""

    @application.post(FOCUS_MEMORY_PATH, tags=["internal", "trusted-memory"])
    def trusted_memory_focus(
        body: FocusMemoryQuery,
        request: Request,
        authorization: str = Header(default="", alias="Authorization"),
    ) -> dict[str, Any]:
        _authorize(request, authorization)
        service: TrustedMemoryFocusService = request.app.state.trusted_memory_focus_service
        return service.fetch(body).model_dump(mode="json")


__all__ = [
    "FOCUS_MEMORY_PATH",
    "TrustedMemoryFocusService",
    "register_trusted_memory_focus_routes",
    "validate_focus_service_token",
]
