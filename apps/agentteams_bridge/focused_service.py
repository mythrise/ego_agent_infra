"""AgentTeams bridge extension that binds trusted focus memory into phase envelopes."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, Mapping, Optional, Protocol, Tuple

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
from .models import (
    BridgeRun,
    CollaborationEnvelope,
    EnvelopeKind,
    ResearchTaskSpec,
    canonical_sha256,
    utc_now,
)
from .service import AgentTeamsBridge, POST_APPROVAL_STAGES
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
    """Original AgentTeams orchestration with deterministic phase focus memory."""

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

    @staticmethod
    def _ordered_tasks(
        tasks: Tuple[ResearchTaskSpec, ...],
    ) -> Tuple[ResearchTaskSpec, ...]:
        return tuple(sorted(tasks, key=lambda task: task.task_id))

    def _focus_tasks(
        self,
        run: BridgeRun,
        kind: EnvelopeKind,
    ) -> Optional[Tuple[ResearchTaskSpec, ...]]:
        if kind is EnvelopeKind.TASK_REQUEST:
            tasks = tuple(self._effective_tasks(run))
        elif kind is EnvelopeKind.APPROVAL_GRANTED:
            tasks = tuple(self._effective_tasks(run, POST_APPROVAL_STAGES))
        else:
            return None
        return self._ordered_tasks(tasks)

    @classmethod
    def _focus_cache_identity(
        cls,
        kind: EnvelopeKind,
        tasks: Tuple[ResearchTaskSpec, ...],
    ) -> Tuple[str, str]:
        task_graph_sha256 = canonical_sha256(
            [task.model_dump(mode="json") for task in cls._ordered_tasks(tasks)]
        )
        return "%s:%s" % (kind.value, task_graph_sha256), task_graph_sha256

    def _status_bundle(
        self,
        run: BridgeRun,
        kind: EnvelopeKind,
        task_graph_sha256: str,
        status: str,
        *,
        failure: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        core: Dict[str, Any] = {
            "schema": _FOCUS_BUNDLE_SCHEMA,
            "status": status,
            "mode": self.focus_memory_mode.value,
            "tenant_id": self.focus_memory_tenant_id,
            "project_id": run.agentteams_project_id,
            "envelope_kind": kind.value,
            "task_graph_sha256": task_graph_sha256,
            "contexts": {},
        }
        if failure is not None:
            core["failure"] = failure
        return {**core, "bundle_sha256": canonical_sha256(core)}

    @staticmethod
    def _validate_bundle_digest(bundle: Mapping[str, Any]) -> Dict[str, Any]:
        digest = bundle.get("bundle_sha256")
        if not isinstance(digest, str):
            raise BridgeError(
                "focus_memory_cache_invalid",
                "Cached focus-memory bundle has no canonical digest",
            )
        core = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
        if canonical_sha256(core) != digest:
            raise BridgeError(
                "focus_memory_cache_invalid",
                "Cached focus-memory bundle failed digest verification",
            )
        return copy.deepcopy(dict(bundle))

    def _cached_focus_bundle(
        self,
        run: BridgeRun,
        *,
        cache_key: str,
        kind: EnvelopeKind,
        task_graph_sha256: str,
    ) -> Optional[Dict[str, Any]]:
        cached = run.checkpoint.get("focus_memory_bundles")
        if cached is None:
            return None
        if not isinstance(cached, dict):
            raise BridgeError(
                "focus_memory_cache_invalid",
                "Focus-memory checkpoint cache is not a JSON object",
            )
        candidate = cached.get(cache_key)
        if candidate is None:
            return None
        if not isinstance(candidate, dict):
            raise BridgeError(
                "focus_memory_cache_invalid",
                "Cached focus-memory bundle is not a JSON object",
            )
        bundle = self._validate_bundle_digest(candidate)
        expected = {
            "mode": self.focus_memory_mode.value,
            "tenant_id": self.focus_memory_tenant_id,
            "project_id": run.agentteams_project_id,
            "envelope_kind": kind.value,
            "task_graph_sha256": task_graph_sha256,
        }
        mismatched = tuple(
            key for key, value in expected.items() if bundle.get(key) != value
        )
        if mismatched:
            raise BridgeError(
                "focus_memory_cache_scope_mismatch",
                "Cached focus-memory bundle is bound to a different phase or scope",
                details={"mismatched": list(mismatched)},
            )
        return bundle

    def _remember_focus_bundle(
        self,
        run: BridgeRun,
        *,
        cache_key: str,
        bundle: Dict[str, Any],
        kind: EnvelopeKind,
        task_graph_sha256: str,
    ) -> Dict[str, Any]:
        validated = self._validate_bundle_digest(bundle)
        cached = run.checkpoint.get("focus_memory_bundles")
        if cached is None:
            cache: Dict[str, Any] = {}
        elif isinstance(cached, dict):
            cache = copy.deepcopy(cached)
        else:
            raise BridgeError(
                "focus_memory_cache_invalid",
                "Focus-memory checkpoint cache is not a JSON object",
            )
        existing = cache.get(cache_key)
        if existing is not None:
            previous = self._cached_focus_bundle(
                run,
                cache_key=cache_key,
                kind=kind,
                task_graph_sha256=task_graph_sha256,
            )
            if previous != validated:
                raise BridgeError(
                    "focus_memory_cache_conflict",
                    "A phase retry produced a different focus-memory bundle",
                )
            assert previous is not None
            return previous
        cache[cache_key] = copy.deepcopy(validated)
        run.checkpoint["focus_memory_bundles"] = cache
        return copy.deepcopy(validated)

    def _archive_focus_receipt(
        self,
        run: BridgeRun,
        fetch: FocusMemoryFetch,
        *,
        cache_key: str,
    ) -> None:
        if fetch.receipt is None:
            return
        receipt_sha256 = canonical_sha256(fetch.receipt)
        receipt_key_sha256 = canonical_sha256(
            {
                "cache_key": cache_key,
                "source_sha256": fetch.source.source_sha256,
                "receipt_sha256": receipt_sha256,
            }
        )
        lease = run.checkpoint.get("_operation_lease")
        lease_owner = lease.get("owner_id") if isinstance(lease, dict) else None
        self.store.archive_receipt(
            run.id,
            receipt_key="focus-memory:%s" % receipt_key_sha256,
            source="egoagentos",
            kind="trusted-memory-focus-source",
            payload=fetch.receipt,
            lease_owner=lease_owner,
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
        tasks: Tuple[ResearchTaskSpec, ...],
        *,
        kind: EnvelopeKind,
        task_graph_sha256: str,
    ) -> Dict[str, Any]:
        if not source.facts:
            empty_core: Dict[str, Any] = {
                "schema": _FOCUS_BUNDLE_SCHEMA,
                "status": "EMPTY",
                "mode": self.focus_memory_mode.value,
                "tenant_id": self.focus_memory_tenant_id,
                "project_id": run.agentteams_project_id,
                "envelope_kind": kind.value,
                "task_graph_sha256": task_graph_sha256,
                "source_sha256": source.source_sha256,
                "memory_snapshot_root": source.memory_snapshot_root,
                "scanned_count": source.scanned_count,
                "matching_count": source.matching_count,
                "contexts": {},
            }
            return {**empty_core, "bundle_sha256": canonical_sha256(empty_core)}

        contexts: Dict[str, Any] = {}
        for task in self._ordered_tasks(tasks):
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

        ready_core: Dict[str, Any] = {
            "schema": _FOCUS_BUNDLE_SCHEMA,
            "status": "READY",
            "mode": self.focus_memory_mode.value,
            "tenant_id": self.focus_memory_tenant_id,
            "project_id": run.agentteams_project_id,
            "envelope_kind": kind.value,
            "task_graph_sha256": task_graph_sha256,
            "source_sha256": source.source_sha256,
            "memory_snapshot_root": source.memory_snapshot_root,
            "scanned_count": source.scanned_count,
            "matching_count": source.matching_count,
            "truncated_by_scan_limit": source.truncated_by_scan_limit,
            "truncated_by_max_items": source.truncated_by_max_items,
            "contexts": contexts,
        }
        return {**ready_core, "bundle_sha256": canonical_sha256(ready_core)}

    def _focus_bundle(
        self,
        run: BridgeRun,
        tasks: Tuple[ResearchTaskSpec, ...],
        *,
        kind: EnvelopeKind,
        cache_key: str,
        task_graph_sha256: str,
    ) -> Dict[str, Any]:
        cached = self._cached_focus_bundle(
            run,
            cache_key=cache_key,
            kind=kind,
            task_graph_sha256=task_graph_sha256,
        )
        if cached is not None:
            return cached

        if self.focus_memory_mode is FocusMemoryMode.DISABLED:
            bundle = self._status_bundle(
                run,
                kind,
                task_graph_sha256,
                "DISABLED",
            )
            return self._remember_focus_bundle(
                run,
                cache_key=cache_key,
                bundle=bundle,
                kind=kind,
                task_graph_sha256=task_graph_sha256,
            )

        assert self.focus_memory_provider is not None
        try:
            fetch = self.focus_memory_provider.fetch(
                tenant_id=self.focus_memory_tenant_id,
                project_id=run.agentteams_project_id,
                max_items=self.focus_memory_source_max_items,
                scan_limit=self.focus_memory_scan_limit,
            )
            self._archive_focus_receipt(run, fetch, cache_key=cache_key)
            self._require_complete_source(fetch.source)
            bundle = self._ready_bundle(
                run,
                fetch.source,
                tasks,
                kind=kind,
                task_graph_sha256=task_graph_sha256,
            )
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
            bundle = self._status_bundle(
                run,
                kind,
                task_graph_sha256,
                "UNAVAILABLE",
                failure=failure,
            )

        return self._remember_focus_bundle(
            run,
            cache_key=cache_key,
            bundle=bundle,
            kind=kind,
            task_graph_sha256=task_graph_sha256,
        )

    def _envelope(
        self,
        run: BridgeRun,
        kind: EnvelopeKind,
        body: Dict[str, Any],
        *,
        attempt: int = 1,
        causation_id: Optional[str] = None,
    ) -> CollaborationEnvelope:
        tasks = self._focus_tasks(run, kind)
        if tasks is None:
            return super()._envelope(
                run,
                kind,
                body,
                attempt=attempt,
                causation_id=causation_id,
            )
        if "focus_memory" in body:
            raise BridgeError(
                "focus_memory_body_conflict",
                "Callers cannot supply an unverified focus-memory envelope field",
            )
        cache_key, task_graph_sha256 = self._focus_cache_identity(kind, tasks)
        projected_body = {
            **body,
            "focus_memory": self._focus_bundle(
                run,
                tasks,
                kind=kind,
                cache_key=cache_key,
                task_graph_sha256=task_graph_sha256,
            ),
        }
        return super()._envelope(
            run,
            kind,
            projected_body,
            attempt=attempt,
            causation_id=causation_id,
        )


__all__ = [
    "EgoTrustedMemoryProvider",
    "FocusMemoryFetch",
    "FocusMemoryMode",
    "FocusMemoryProvider",
    "FocusedAgentTeamsBridge",
]
