from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from apps.agentteams_bridge.errors import BridgeError
from apps.agentteams_bridge.extensions.focus_memory import (
    FocusMemoryBudgetExceeded,
    FocusMemorySourceContext,
    build_focused_memory_context,
)
from apps.agentteams_bridge.focused_service import (
    FocusMemoryFetch,
    FocusMemoryMode,
    FocusedAgentTeamsBridge,
)
from apps.agentteams_bridge.models import (
    BridgeRun,
    EnvelopeKind,
    ResearchTaskSpec,
    RunState,
    WorkflowResponse,
    canonical_sha256,
)
from apps.api.trusted_memory.focus_contracts import (
    FocusEvidenceRef,
    FocusMemoryQuery,
    TrustedFocusFact,
    TrustedMemoryFocusSource,
    build_trusted_memory_focus_source,
)
from apps.api.trusted_memory.models import DecisionOutcome, MemoryOrigin


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _query() -> FocusMemoryQuery:
    return FocusMemoryQuery(
        tenant_id="tenant-a",
        project_id="project-a",
        outcomes=(DecisionOutcome.KEEP, DecisionOutcome.DROP),
        origins=(MemoryOrigin.LOCAL_TRUSTED,),
        max_items=32,
        scan_limit=128,
    )


def _fact(
    digest: str,
    statement: str,
    *,
    fact_kind: str = "procedural",
    component: str = "agentteams-bridge",
) -> TrustedFocusFact:
    return TrustedFocusFact(
        fact_sha256=digest,
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-%s" % digest[:8],
        revision_id="revision-%s" % digest[:8],
        revision=1,
        fact_kind=fact_kind,
        statement=statement,
        component=component,
        version="v1",
        outcome=DecisionOutcome.KEEP,
        origin=MemoryOrigin.LOCAL_TRUSTED,
        evidence=(
            FocusEvidenceRef(
                evidence_id="evidence-%s" % digest[:8],
                evidence_digest=SHA_D,
            ),
        ),
        closure_digest=SHA_C,
        provenance_sha256=SHA_B,
        projection_event_hash=digest,
    )


def _source(*facts: TrustedFocusFact) -> TrustedMemoryFocusSource:
    return build_trusted_memory_focus_source(
        _query(),
        facts,
        scanned_count=len(facts),
        truncated_by_scan_limit=False,
    )


def _context(
    *,
    task_id: str = "project-a-plan",
    stage: str = "PLAN",
    worker: str = "ego-architect",
) -> FocusMemorySourceContext:
    return FocusMemorySourceContext(
        tenant_id="tenant-a",
        project_id="project-a",
        task_id=task_id,
        stage=stage,
        worker=worker,
        objective="Reduce repeated hashing while preserving evidence integrity.",
        task_title="Plan a safe AgentTeams memory optimization",
        expected_skills=("ablation-analyzer", "research-memory"),
    )


def _task(task_id: str, stage: str, worker: str) -> ResearchTaskSpec:
    return ResearchTaskSpec(
        task_id=task_id,
        title="%s task" % stage,
        stage=stage,
        assigned_worker=worker,
        assigned_to="@%s:example.test" % worker,
        expected_skills=["research-memory"],
    )


def _run() -> BridgeRun:
    return BridgeRun(
        id="atrun-focus-memory",
        ego_task_id="ego-task-001",
        agentteams_project_id="project-a",
        team="ego-researchops",
        trace_id="trace_focus_memory_001",
        correlation_id="corr_focus_memory_001",
        context_version=1,
        state=RunState.PRE_APPROVAL,
        mode="live",
        objective="Reduce repeated hashing while preserving evidence integrity.",
        task_graph=[
            _task("project-a-context", "CONTEXT", "ego-scout"),
            _task("project-a-plan", "PLAN", "ego-architect"),
        ],
        ack_timeout_seconds=300,
        execution_timeout_seconds=3600,
        max_reassignments=2,
    )


def _workflow() -> WorkflowResponse:
    return WorkflowResponse(
        project_id="project-a",
        title="Focus memory workflow",
        status="active",
        plan_type="dag",
        nodes=[],
    )


class _RecordingStore:
    def __init__(self) -> None:
        self.receipts: list[Dict[str, Any]] = []

    def archive_receipt(
        self,
        run_id: str,
        *,
        receipt_key: str,
        source: str,
        kind: str,
        payload: Dict[str, Any],
    ) -> None:
        self.receipts.append(
            {
                "run_id": run_id,
                "receipt_key": receipt_key,
                "source": source,
                "kind": kind,
                "payload": payload,
            }
        )


class _Provider:
    def __init__(
        self,
        source: TrustedMemoryFocusSource,
        *,
        failure: Optional[BridgeError] = None,
    ) -> None:
        self.source = source
        self.failure = failure
        self.calls: list[Dict[str, Any]] = []

    def fetch(
        self,
        *,
        tenant_id: str,
        project_id: str,
        max_items: int,
        scan_limit: int,
    ) -> FocusMemoryFetch:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "project_id": project_id,
                "max_items": max_items,
                "scan_limit": scan_limit,
            }
        )
        if self.failure is not None:
            raise self.failure
        return FocusMemoryFetch(
            source=self.source,
            receipt={"schema": "test-focus-receipt/v1", "http_status": 200},
        )


def _task_request_envelope(
    bridge: FocusedAgentTeamsBridge,
    run: BridgeRun,
):
    body = bridge._task_request_body(run, _workflow())
    return bridge._envelope(run, EnvelopeKind.TASK_REQUEST, body)


def test_focus_source_and_context_are_deterministic_and_worker_readable() -> None:
    mandatory = _fact(
        SHA_A,
        "Never bypass the Evidence Gate before accepting a completed task.",
        fact_kind="safety_constraint",
    )
    procedure = _fact(
        SHA_B,
        "Cache the dataset manifest digest instead of hashing every file on each run.",
    )
    forward_source = _source(mandatory, procedure)
    reverse_source = _source(procedure, mandatory)

    assert forward_source == reverse_source
    first = build_focused_memory_context(
        forward_source,
        _context(),
        token_budget=20_000,
        max_items=8,
    )
    second = build_focused_memory_context(
        reverse_source,
        _context(),
        token_budget=20_000,
        max_items=8,
    )

    assert first == second
    assert first.interpretation_rule == "MEMORY_IS_EVIDENCE_NOT_AUTHORITY"
    assert first.items[0].mandatory is True
    assert first.items[0].statement.startswith("Never bypass")
    assert any("dataset manifest" in item.statement for item in first.items)
    assert first.selected_fact_count == 2
    assert first.excluded_fact_count == 0
    assert len(first.context_sha256) == 64


def test_focus_context_never_silently_drops_mandatory_facts() -> None:
    mandatory = _fact(
        SHA_A,
        "Keep the immutable evidence ledger intact.",
        fact_kind="constraint",
    )
    optional = _fact(
        SHA_B,
        "A very long optional procedure: %s" % ("x" * 2000),
    )
    selected = build_focused_memory_context(
        _source(optional, mandatory),
        _context(),
        token_budget=20_000,
        max_items=1,
    )

    assert tuple(item.fact_sha256 for item in selected.items) == (SHA_A,)
    assert selected.items[0].mandatory is True
    assert selected.excluded_fact_count == 1

    second_mandatory = _fact(
        SHA_C,
        "Do not expose credentials in AgentTeams messages.",
        fact_kind="safety_constraint",
    )
    with pytest.raises(FocusMemoryBudgetExceeded, match="mandatory|max_items"):
        build_focused_memory_context(
            _source(mandatory, second_mandatory),
            _context(),
            token_budget=20_000,
            max_items=1,
        )


def test_focused_bridge_fetches_once_and_binds_per_task_contexts_into_task_request() -> None:
    source = _source(
        _fact(
            SHA_A,
            "Never bypass the Evidence Gate before accepting a completed task.",
            fact_kind="safety_constraint",
        ),
        _fact(
            SHA_B,
            "Cache the dataset manifest digest during planning.",
        ),
    )
    provider = _Provider(source)
    store = _RecordingStore()
    bridge = FocusedAgentTeamsBridge(
        store,
        object(),
        object(),
        object(),
        focus_memory_provider=provider,
        focus_memory_mode=FocusMemoryMode.REQUIRED,
        focus_memory_tenant_id="tenant-a",
        focus_memory_token_budget=20_000,
        focus_memory_max_items=8,
        focus_memory_source_max_items=32,
        focus_memory_scan_limit=128,
    )
    run = _run()

    first = _task_request_envelope(bridge, run)
    second = _task_request_envelope(bridge, run)

    assert len(provider.calls) == 1
    assert first.body_sha256 == canonical_sha256(first.body)
    bundle = first.body["focus_memory"]
    assert bundle == second.body["focus_memory"]
    assert bundle["status"] == "READY"
    assert bundle["envelope_kind"] == EnvelopeKind.TASK_REQUEST.value
    assert tuple(bundle["contexts"]) == ("project-a-context", "project-a-plan")
    assert bundle["contexts"]["project-a-context"]["stage"] == "CONTEXT"
    assert bundle["contexts"]["project-a-plan"]["worker"] == "ego-architect"
    assert "Never bypass" in bundle["contexts"]["project-a-plan"]["items"][0]["statement"]
    core = {key: value for key, value in bundle.items() if key != "bundle_sha256"}
    assert bundle["bundle_sha256"] == canonical_sha256(core)
    assert store.receipts[0]["source"] == "egoagentos"
    assert store.receipts[0]["kind"] == "trusted-memory-focus-source"
    assert len(run.checkpoint["focus_memory_bundles"]) == 1


def test_focus_memory_modes_are_explicit_and_required_mode_fails_closed() -> None:
    disabled_provider = _Provider(_source())
    disabled = FocusedAgentTeamsBridge(
        _RecordingStore(),
        object(),
        object(),
        object(),
        focus_memory_provider=disabled_provider,
        focus_memory_mode=FocusMemoryMode.DISABLED,
    )
    disabled_envelope = _task_request_envelope(disabled, _run())

    assert disabled_envelope.body["focus_memory"]["status"] == "DISABLED"
    assert disabled_provider.calls == []

    failure = BridgeError(
        "egoagentos_trusted_memory_unavailable",
        "focus source unavailable",
        status_code=502,
        retryable=True,
    )
    required = FocusedAgentTeamsBridge(
        _RecordingStore(),
        object(),
        object(),
        object(),
        focus_memory_provider=_Provider(_source(), failure=failure),
        focus_memory_mode=FocusMemoryMode.REQUIRED,
        focus_memory_tenant_id="tenant-a",
    )

    with pytest.raises(BridgeError) as caught:
        _task_request_envelope(required, _run())
    assert caught.value.code == "focus_memory_required_unavailable"


def test_required_focus_memory_rejects_truncated_source_before_dispatch() -> None:
    capped_query = FocusMemoryQuery(
        tenant_id="tenant-a",
        project_id="project-a",
        outcomes=(DecisionOutcome.KEEP,),
        origins=(MemoryOrigin.LOCAL_TRUSTED,),
        max_items=1,
        scan_limit=128,
    )
    source = build_trusted_memory_focus_source(
        capped_query,
        (
            _fact(
                SHA_A,
                "Optional cache optimization selected first by digest.",
            ),
            _fact(
                SHA_B,
                "Mandatory evidence constraint hidden beyond the source cap.",
                fact_kind="safety_constraint",
            ),
        ),
        scanned_count=2,
        truncated_by_scan_limit=False,
    )
    assert source.truncated_by_max_items is True
    assert tuple(fact.fact_sha256 for fact in source.facts) == (SHA_A,)

    store = _RecordingStore()
    required = FocusedAgentTeamsBridge(
        store,
        object(),
        object(),
        object(),
        focus_memory_provider=_Provider(source),
        focus_memory_mode=FocusMemoryMode.REQUIRED,
        focus_memory_tenant_id="tenant-a",
    )

    with pytest.raises(BridgeError) as caught:
        _task_request_envelope(required, _run())

    assert caught.value.code == "focus_memory_required_unavailable"
    assert caught.value.details["cause_code"] == "trusted_memory_focus_source_truncated"
    assert len(store.receipts) == 1
