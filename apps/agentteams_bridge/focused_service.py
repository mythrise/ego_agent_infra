"""AgentTeams bridge extension that binds trusted focus memory into TASK_REQUEST."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, Optional, Protocol

from apps.api.trusted_memory.focus_contracts import (
    FocusMemoryQuery,
    TrustedMemoryFocusSource,
)
from apps.api.trusted_memory.models import DecisionOutcome, MemoryOrigin

from .clients import JSONClient
from .errors import BridgeError
from .extensions.focus_memory import (
    FocusMemoryBudgetExceeded,
    FocusMemorySourceContext,
    build_focused_memory_context,
)
from .models import BridgeRun, WorkflowResponse, canonical_sha256, utc_now
from .service import AgentTeamsBridge
from .transport import HTTPTransport


_FOCUS_BUNDLE_SCHEMA = "egoagentos.agentteams-focus-memory-bundle/v1"


class FocusMemoryMode(str, Enum):
    DISABLED = "disabled"
    BEST_EFFORT = "best_effort"
    REQUIRED = "required"


@dataclass(frozen=True)
class FocusMemoryFetch:
    source: TrustedMemoryFocusSource
    receipt: Optional[Dict[str, Any]] = None


class FocusMemoryProvider(Protocol):
    def fetch(
        self,
        *,
        tenant_id: str,
        project_id: str,
        max_items: int,
        scan_limit: int,
    ) -> FocusMemoryFetch: ...


class EgoTrustedMemoryProvider(JSONClient):
    """Authenticated client for the API's internal trusted-memory focus endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        service_token: str,
        timeout: float = 15.0,
        transport: Optional[HTTPTransport] = None,
    ) -> None:
        if len(service_token.encode("utf-8")) < 32:
            raise ValueError("trusted-memory service token must contain at least 32 bytes")
        super().__init__(
            base_url,
            token=service_token,
            timeout=timeout,
            transport=transport,
            upstream_name="egoagentos",
        )

    def fetch(
        self,
        *,
        tenant_id: str,
        project_id: str,
        max_items: int,
        scan_limit: int,
    ) -> FocusMemoryFetch:
        query = FocusMemoryQuery(
            tenant_id=tenant_id,
            project_id=project_id,
            outcomes=(
                DecisionOutcome.DROP,
                DecisionOutcome.INCONCLUSIVE,
                DecisionOutcome.KEEP,
            ),
            origins=(
                MemoryOrigin.ATTESTED_EXTERNAL,
                MemoryOrigin.LOCAL_TRUSTED,
            ),
            max_items=max_items,
            scan_limit=scan_limit,
        )
        payload, receipt = self.request_json_with_receipt(
            "POST",
            "/api/v1/internal/trusted-memory/focus",
            body=query.model_dump(mode="json"),
            operation="fetch-trusted-memory-focus",
        )
        if not isinstance(payload, dict):
            raise BridgeError(
                "egoagentos_trusted_memory_malformed",
                "Trusted-memory focus source response is not a JSON object",
                status_code=502,
            )
        try:
            source = TrustedMemoryFocusSource.model_validate(payload)
        except (TypeError, ValueError) as error:
            raise BridgeError(
                "egoagentos_trusted_memory_malformed",
                "Trusted-memory focus source failed canonical validation",
                status_code=502,
                details={"error_type": type(error).__name__},
            ) from error
        return FocusMemoryFetch(source=source, receipt=receipt)


class FocusedAgentTeamsBridge(AgentTeamsBridge):
    """Original AgentTeams orchestration with a deterministic focus-memory projection."""

    def __init__(
        self,
        store: Any,
        agentteams: Any,
        matrix: Any,
        ego: Any,
        *,
        focus_memory_provider: Optional[FocusMemoryProvider] = None,
        focus_memory_mode: FocusMemoryMode = FocusMemoryMode.DISABLED,
        focus_memory_tenant_id: str = "local",
        focus_memory_token_budget: int = 4000,
        focus_memory_max_items: int = 12,
        focus_memory_source_max_items: int = 64,
        focus_memory_scan_limit: int = 512,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        super().__init__(store, agentteams, matrix, ego, clock=clock)
        self.focus_memory_provider = focus_memory_provider
        self.focus_memory_mode = FocusMemoryMode(focus_memory_mode)
        self.focus_memory_tenant_id = focus_memory_tenant_id
        self.focus_memory_token_budget = focus_memory_token_budget
        self.focus_memory_max_items = focus_memory_max_items
        self.focus_memory_source_max_items = focus_memory_source_max_items
        self.focus_memory_scan_limit = focus_memory_scan_limit
        self._validate_focus_configuration()

    def _validate_focus_configuration(self) -> None:
        if not self.focus_memory_tenant_id or len(self.focus_memory_tenant_id) > 200:
            raise ValueError("focus-memory tenant ID must contain 1-200 characters")
        values = {
            "focus_memory_token_budget": self.focus_memory_token_budget,
            "focus_memory_max_items": self.focus_memory_max_items,
            "focus_memory_source_max_items": self.focus_memory_source_max_items,
            "focus_memory_scan_limit": self.focus_memory_scan_limit,
        }
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("%s must be a positive integer" % name)
        if self.focus_memory_max_items > self.focus_memory_source_max_items:
            raise ValueError("focus context max_items cannot exceed source max_items")
        if (
            self.focus_memory_mode is not FocusMemoryMode.DISABLED
            and self.focus_memory_provider is None
        ):
            raise ValueError("enabled focus-memory mode requires a provider")

    def _status_bundle(
        self,
        status: str,
        *,
        failure: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        core: Dict[str, Any] = {
            "schema": _FOCUS_BUNDLE_SCHEMA,
            "status": status,
            "mode": self.focus_memory_mode.value,
            "contexts": {},
        }
        if failure is not None:
            core["failure"] = failure
        return {**core, "bundle_sha256": canonical_sha256(core)}

    def _archive_focus_receipt(self, run: BridgeRun, fetch: FocusMemoryFetch) -> None:
        if fetch.receipt is None:
            return
        self.store.archive_receipt(
            run.id,
            receipt_key="focus-memory:%s" % fetch.source.source_sha256,
            source="egoagentos",
            kind="trusted-memory-focus-source",
            payload=fetch.receipt,
        )

    @staticmethod
    def _require_complete_source(source: TrustedMemoryFocusSource) -> None:
        """Reject an upstream cap before it can hide an unknown mandatory fact."""

        if not (source.truncated_by_scan_limit or source.truncated_by_max_items):
            return
        raise BridgeError(
            "trusted_memory_focus_source_truncated",
            "Trusted-memory focus source was truncated before mandatory coverage was proven",
            status_code=502,
            retryable=False,
            details={
                "matching_count": source.matching_count,
                "returned_count": len(source.facts),
                "scanned_count": source.scanned_count,
                "truncated_by_scan_limit": source.truncated_by_scan_limit,
                "truncated_by_max_items": source.truncated_by_max_items,
            },
        )

    def _ready_bundle(
        self,
        run: BridgeRun,
        source: TrustedMemoryFocusSource,
    ) -> Dict[str, Any]:
        if not source.facts:
            core: Dict[str, Any] = {
                "schema": _FOCUS_BUNDLE_SCHEMA,
                "status": "EMPTY",
                "mode": self.focus_memory_mode.value,
                "source_sha256": source.source_sha256,
                "memory_snapshot_root": source.memory_snapshot_root,
                "scanned_count": source.scanned_count,
                "matching_count": source.matching_count,
                "contexts": {},
            }
            return {**core, "bundle_sha256": canonical_sha256(core)}

        contexts: Dict[str, Any] = {}
        for task in run.task_graph:
            context = FocusMemorySourceContext(
                tenant_id=self.focus_memory_tenant_id,
                project_id=run.agentteams_project_id,
                task_id=task.task_id,
                stage=task.stage,
                worker=task.assigned_worker,
                objective=run.objective,
                task_title=task.title,
                expected_skills=tuple(task.expected_skills),
            )
            compiled = build_focused_memory_context(
                source,
                context,
                token_budget=self.focus_memory_token_budget,
                max_items=self.focus_memory_max_items,
            )
            contexts[task.task_id] = compiled.model_dump(mode="json")

        core: Dict[str, Any] = {
            "schema": _FOCUS_BUNDLE_SCHEMA,
            "status": "READY",
            "mode": self.focus_memory_mode.value,
            "source_sha256": source.source_sha256,
            "memory_snapshot_root": source.memory_snapshot_root,
            "scanned_count": source.scanned_count,
            "matching_count": source.matching_count,
            "truncated_by_scan_limit": source.truncated_by_scan_limit,
            "truncated_by_max_items": source.truncated_by_max_items,
            "contexts": contexts,
        }
        return {**core, "bundle_sha256": canonical_sha256(core)}

    def _focus_bundle(self, run: BridgeRun) -> Dict[str, Any]:
        if self.focus_memory_mode is FocusMemoryMode.DISABLED:
            return self._status_bundle("DISABLED")
        assert self.focus_memory_provider is not None
        try:
            fetch = self.focus_memory_provider.fetch(
                tenant_id=self.focus_memory_tenant_id,
                project_id=run.agentteams_project_id,
                max_items=self.focus_memory_source_max_items,
                scan_limit=self.focus_memory_scan_limit,
            )
            self._archive_focus_receipt(run, fetch)
            self._require_complete_source(fetch.source)
            return self._ready_bundle(run, fetch.source)
        except (BridgeError, FocusMemoryBudgetExceeded, TypeError, ValueError) as error:
            if self.focus_memory_mode is FocusMemoryMode.REQUIRED:
                cause_code = error.code if isinstance(error, BridgeError) else type(error).__name__
                raise BridgeError(
                    "focus_memory_required_unavailable",
                    "Required trusted focus memory could not be bound to AgentTeams dispatch",
                    status_code=502,
                    retryable=isinstance(error, BridgeError) and error.retryable,
                    details={"cause_code": cause_code},
                ) from error
            failure = {
                "code": error.code if isinstance(error, BridgeError) else type(error).__name__,
                "retryable": isinstance(error, BridgeError) and error.retryable,
            }
            return self._status_bundle("UNAVAILABLE", failure=failure)

    def _task_request_body(  # type: ignore[override]
        self,
        run: BridgeRun,
        workflow: WorkflowResponse,
    ) -> Dict[str, Any]:
        # The base implementation is a static pure helper. This bound specialization
        # intentionally adds one instance-scoped projection before Matrix dispatch.
        body = super()._task_request_body(run, workflow)
        body["focus_memory"] = self._focus_bundle(run)
        return body


__all__ = [
    "EgoTrustedMemoryProvider",
    "FocusMemoryFetch",
    "FocusMemoryMode",
    "FocusMemoryProvider",
    "FocusedAgentTeamsBridge",
]
