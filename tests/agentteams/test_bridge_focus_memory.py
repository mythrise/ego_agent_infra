from __future__ import annotations

import json
from types import ModuleType
from typing import Any

import pytest

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


def _focus_module() -> ModuleType:
    try:
        from apps.agentteams_bridge.extensions import focus_memory
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(f"AgentTeams focus-memory module is missing: {exc}")
    return focus_memory


def _query() -> FocusMemoryQuery:
    return FocusMemoryQuery(
        tenant_id="tenant-1",
        project_id="project-1",
        outcomes=(DecisionOutcome.KEEP,),
        origins=(MemoryOrigin.LOCAL_TRUSTED,),
        max_items=8,
        scan_limit=32,
    )


def _fact(
    digest: str,
    *,
    statement: str,
    fact_kind: str = "procedure",
    component: str = "bridge",
    version: str = "v1",
) -> TrustedFocusFact:
    return TrustedFocusFact(
        fact_sha256=digest,
        tenant_id="tenant-1",
        project_id="project-1",
        lineage_id=f"lineage-{digest[:4]}",
        revision_id=f"revision-{digest[:4]}",
        revision=1,
        fact_kind=fact_kind,
        statement=statement,
        component=component,
        version=version,
        outcome=DecisionOutcome.KEEP,
        origin=MemoryOrigin.LOCAL_TRUSTED,
        evidence_commitment=build_focus_evidence_commitment(
            evidence_ids=(f"evidence-{digest[:4]}",),
            evidence_digests=(SHA_D,),
            decision_closure_digest=SHA_C,
        ),
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


def _context(**overrides: Any) -> Any:
    module = _focus_module()
    values: dict[str, Any] = {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "task_id": "project-1-plan",
        "stage": "PLAN",
        "worker": "ego-architect",
        "objective": "Improve workspace recovery without weakening evidence integrity",
        "task_title": "Plan workspace recovery changes",
        "expected_skills": ("research-plan", "research-memory"),
    }
    values.update(overrides)
    return module.FocusMemorySourceContext.model_validate(values)


def test_focus_context_is_byte_deterministic_and_worker_readable() -> None:
    module = _focus_module()
    facts = (
        _fact(
            SHA_A,
            statement="Use the exact workspace checkpoint before any recovery write.",
            fact_kind="procedure",
            component="workspace",
        ),
        _fact(
            SHA_B,
            statement="A reviewer must independently verify restored evidence.",
            fact_kind="constraint",
            component="evidence",
        ),
    )
    source = _source(*facts)

    first = module.build_focused_memory_context(
        source,
        _context(),
        token_budget=20_000,
        max_items=8,
    )
    second = module.build_focused_memory_context(
        _source(*reversed(facts)),
        _context(),
        token_budget=20_000,
        max_items=8,
    )

    assert first == second
    assert first.items
    assert first.items[0].statement
    assert first.items[0].evidence_commitment.evidence_ids
    assert first.items[0].evidence_commitment.association == (
        "UNPAIRED_SETS_BOUND_BY_DECISION_CLOSURE"
    )
    assert all(len(item.fact_sha256) == 64 for item in first.items)
    assert first.interpretation_rule == "MEMORY_IS_EVIDENCE_NOT_AUTHORITY"
    encoded = json.dumps(
        first.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    assert b"workspace checkpoint" in encoded
    assert len(first.context_sha256) == 64


def test_focus_context_never_drops_mandatory_fact_under_item_cap() -> None:
    module = _focus_module()
    optional = _fact(
        SHA_A,
        statement="Try a smaller batch size for local experiments.",
        fact_kind="tip",
        component="training",
    )
    mandatory = _fact(
        SHA_B,
        statement="Never alter accepted evidence outside the immutable ledger.",
        fact_kind="constraint",
        component="evidence",
    )

    context = module.build_focused_memory_context(
        _source(optional, mandatory),
        _context(),
        token_budget=20_000,
        max_items=1,
    )

    assert len(context.items) == 1
    assert context.items[0].fact_sha256 == SHA_B
    assert context.items[0].mandatory is True
    assert context.excluded_fact_count == 1


def test_focus_context_fails_closed_when_mandatory_fact_cannot_fit() -> None:
    module = _focus_module()
    mandatory = _fact(
        SHA_A,
        statement="M" * 3000,
        fact_kind="constraint",
        component="evidence",
    )

    with pytest.raises(module.FocusMemoryBudgetExceeded, match="mandatory|budget"):
        module.build_focused_memory_context(
            _source(mandatory),
            _context(),
            token_budget=1000,
            max_items=1,
        )


def test_focus_context_rejects_cross_scope_source() -> None:
    module = _focus_module()
    source = _source(
        _fact(
            SHA_A,
            statement="Use the project checkpoint.",
        )
    )

    with pytest.raises(ValueError, match="project|scope"):
        module.build_focused_memory_context(
            source,
            _context(project_id="project-other"),
            token_budget=20_000,
            max_items=8,
        )


def test_focus_context_ranking_changes_by_stage_without_changing_source_truth() -> None:
    module = _focus_module()
    procedure = _fact(
        SHA_A,
        statement="Execute the exact allowlisted recovery procedure.",
        fact_kind="procedure",
        component="runtime",
    )
    failure = _fact(
        SHA_B,
        statement="The prior observation failed when trace evidence was missing.",
        fact_kind="failure",
        component="trace",
    )
    source = _source(procedure, failure)

    execute = module.build_focused_memory_context(
        source,
        _context(stage="EXECUTE", worker="ego-runtime"),
        token_budget=20_000,
        max_items=8,
    )
    observe = module.build_focused_memory_context(
        source,
        _context(stage="OBSERVE", worker="ego-runtime"),
        token_budget=20_000,
        max_items=8,
    )

    assert execute.source_sha256 == observe.source_sha256
    assert execute.memory_snapshot_root == observe.memory_snapshot_root
    assert execute.items[0].fact_sha256 == SHA_A
    assert observe.items[0].fact_sha256 == SHA_B
