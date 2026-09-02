from __future__ import annotations

from typing import Any, Dict

from apps.agentteams_bridge.extensions.focus_memory import FocusMemorySourceContext
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
    canonical_sha256,
)
from apps.api.trusted_memory.focus_contracts import (
    FocusMemoryQuery,
    TrustedFocusFact,
    TrustedMemoryFocusSource,
    build_focus_evidence_commitment,
    build_trusted_memory_focus_source,
)
from apps.api.trusted_memory.models import DecisionOutcome, MemoryOrigin


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _task(task_id: str, stage: str, worker: str, *, status: str = "planned") -> ResearchTaskSpec:
    return ResearchTaskSpec(
        task_id=task_id,
        title="%s task" % stage,
        stage=stage,
        assigned_worker=worker,
        assigned_to="@%s:example.test" % worker,
        expected_skills=["research-memory"],
        status=status,
    )


def _run() -> BridgeRun:
    return BridgeRun(
        id="atrun-post-focus",
        ego_task_id="ego-task-001",
        agentteams_project_id="project-a",
        team="ego-researchops",
        trace_id="trace_post_focus_001",
        correlation_id="corr_post_focus_001",
        context_version=1,
        state=RunState.POST_APPROVAL,
        mode="live",
        objective="Execute and verify a safe memory optimization.",
        task_graph=[
            _task("project-a-context", "CONTEXT", "ego-scout", status="completed"),
            _task("project-a-plan", "PLAN", "ego-architect", status="completed"),
            _task("project-a-execute", "EXECUTE", "ego-runtime"),
            _task("project-a-observe", "OBSERVE", "ego-runtime"),
            _task("project-a-evaluate", "EVALUATE", "ego-evaluator"),
            _task("project-a-verify", "VERIFY", "ego-reviewer"),
        ],
        checkpoint={},
        ack_timeout_seconds=300,
        execution_timeout_seconds=3600,
        max_reassignments=2,
    )


def _source() -> TrustedMemoryFocusSource:
    query = FocusMemoryQuery(
        tenant_id="tenant-a",
        project_id="project-a",
        outcomes=(DecisionOutcome.KEEP,),
        origins=(MemoryOrigin.LOCAL_TRUSTED,),
        max_items=16,
        scan_limit=128,
    )
    fact = TrustedFocusFact(
        fact_sha256=SHA_A,
        tenant_id="tenant-a",
        project_id="project-a",
        lineage_id="lineage-a",
        revision_id="revision-a",
        revision=1,
        fact_kind="safety_constraint",
        statement="Keep the Evidence Gate and checkpoint recovery active during execution.",
        component="agentteams-bridge",
        version="v1",
        outcome=DecisionOutcome.KEEP,
        origin=MemoryOrigin.LOCAL_TRUSTED,
        evidence_commitment=build_focus_evidence_commitment(
            evidence_ids=("evidence-a",),
            evidence_digests=(SHA_D,),
            decision_closure_digest=SHA_C,
        ),
        provenance_sha256=SHA_B,
        projection_event_hash=SHA_A,
    )
    return build_trusted_memory_focus_source(
        query,
        (fact,),
        scanned_count=1,
        truncated_by_scan_limit=False,
    )


class _Store:
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
        lease_owner: Optional[str] = None,
    ) -> None:
        self.receipts.append(
            {
                "run_id": run_id,
                "receipt_key": receipt_key,
                "source": source,
                "kind": kind,
                "payload": payload,
                "lease_owner": lease_owner,
            }
        )


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(
        self,
        *,
        tenant_id: str,
        project_id: str,
        max_items: int,
        scan_limit: int,
    ) -> FocusMemoryFetch:
        self.calls += 1
        return FocusMemoryFetch(
            source=_source(),
            receipt={"schema": "test-focus-receipt/v1", "http_status": 200},
        )


def test_approval_granted_binds_only_post_approval_contexts_and_reuses_frozen_bundle() -> None:
    run = _run()
    provider = _Provider()
    store = _Store()
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
        focus_memory_source_max_items=16,
        focus_memory_scan_limit=128,
    )
    approval_body = {
        "risk_level": "R2",
        "post_approval_task_graph_sha256": canonical_sha256(
            [task.model_dump(mode="json") for task in run.task_graph]
        ),
    }

    first = bridge._envelope(run, EnvelopeKind.APPROVAL_GRANTED, approval_body)
    second = bridge._envelope(run, EnvelopeKind.APPROVAL_GRANTED, approval_body)

    assert first.body_sha256 == canonical_sha256(first.body)
    assert first.body["focus_memory"] == second.body["focus_memory"]
    assert provider.calls == 1
    contexts = first.body["focus_memory"]["contexts"]
    assert tuple(contexts) == (
        "project-a-evaluate",
        "project-a-execute",
        "project-a-observe",
        "project-a-verify",
    )
    assert all(
        FocusMemorySourceContext.model_validate(
            {
                "tenant_id": value["tenant_id"],
                "project_id": value["project_id"],
                "task_id": value["task_id"],
                "stage": value["stage"],
                "worker": value["worker"],
                "objective": value["objective"],
                "task_title": value["task_title"],
                "expected_skills": value["expected_skills"],
            }
        ).stage
        in {"EXECUTE", "OBSERVE", "EVALUATE", "VERIFY"}
        for value in contexts.values()
    )
    assert all(
        value["items"][0]["evidence_commitment"]["association"]
        == "UNPAIRED_SETS_BOUND_BY_DECISION_CLOSURE"
        for value in contexts.values()
    )
    assert len(run.checkpoint["focus_memory_bundles"]) == 1
    assert len(store.receipts) == 1
