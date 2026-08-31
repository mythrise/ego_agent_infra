from __future__ import annotations

import importlib
from types import ModuleType
from typing import Any

import pytest

from apps.agentteams_bridge.extensions import AttentionFactRef


SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64


def _attention_module() -> ModuleType:
    try:
        return importlib.import_module("apps.agentteams_bridge.extensions.attention")
    except ModuleNotFoundError:
        pytest.fail("the deterministic attention compiler is missing")


def _context(**overrides: Any) -> Any:
    module = _attention_module()
    values: dict[str, Any] = {
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "task_id": "task-1",
        "turn": 2,
        "generation": 1,
        "requirement_ledger_sha256": SHA_A,
        "workspace_checkpoint_sha256": SHA_B,
        "policy_sha256": SHA_C,
        "memory_watermark": 12,
        "current_requirement_text": "Keep the report grounded in accepted evidence.",
        "unresolved_failure_ids": ("failure.test",),
        "unresolved_conflict_ids": ("conflict.fact",),
        "mandatory_policy_constraint_ids": ("constraint.workspace-bound",),
        "explicit_exclusion_digests": (),
    }
    values.update(overrides)
    return module.AttentionSourceContext.model_validate(values)


def _fact(
    digest: str,
    *,
    relevance: int = 9000,
    **overrides: Any,
) -> AttentionFactRef:
    values: dict[str, Any] = {
        "fact_sha256": digest,
        "tenant_id": "tenant-1",
        "project_id": "project-1",
        "component": "bridge",
        "version": "v1",
        "outcome": "verified",
        "origin": "signed-evaluator",
        "lifecycle": "VALIDATED",
        "relevance_score_basis_points": relevance,
        "evidence_watermark": 12,
    }
    values.update(overrides)
    return AttentionFactRef.model_validate(values)


def test_attention_preserves_mandatory_capsule_and_is_byte_deterministic() -> None:
    module = _attention_module()
    context = _context(
        unresolved_failure_ids=("failure.z", "failure.a"),
        unresolved_conflict_ids=("conflict.b", "failure.a"),
        mandatory_policy_constraint_ids=("constraint.z", "constraint.a"),
    )
    facts = (_fact(SHA_B, relevance=8000), _fact(SHA_A, relevance=9000))

    first = module.build_attention_packet(context, facts, token_budget=20_000)
    second = module.build_attention_packet(context, tuple(reversed(facts)), token_budget=20_000)

    assert first == second
    assert first.current_requirement_text == context.current_requirement_text
    assert first.workspace_checkpoint_sha256 == SHA_B
    assert first.policy_sha256 == SHA_C
    assert first.unresolved_failure_ids == (
        "conflict.b",
        "failure.a",
        "failure.z",
    )
    assert first.mandatory_policy_constraint_ids == ("constraint.a", "constraint.z")
    assert tuple(fact.fact_sha256 for fact in first.eligible_fact_refs) == (SHA_A, SHA_B)
    assert len(first.source_context_sha256) == 64
    assert len(first.packet_sha256) == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("tenant_id", "tenant-other"),
        ("project_id", "project-other"),
        ("evidence_watermark", 11),
        ("lifecycle", "SUPERSEDED"),
    ],
)
def test_attention_rejects_cross_scope_stale_or_ineligible_refs(
    field: str, value: object
) -> None:
    module = _attention_module()
    fact = _fact(SHA_A, **{field: value})

    with pytest.raises(ValueError, match="tenant|project|watermark|lifecycle|VALIDATED"):
        module.build_attention_packet(_context(), (fact,), token_budget=20_000)


def test_attention_sorts_equal_relevance_by_digest_and_rejects_duplicates() -> None:
    module = _attention_module()
    facts = (
        _fact(SHA_C, relevance=9000),
        _fact(SHA_A, relevance=9000),
        _fact(SHA_B, relevance=9500),
    )

    packet = module.build_attention_packet(_context(), facts, token_budget=20_000)

    assert tuple(fact.fact_sha256 for fact in packet.eligible_fact_refs) == (
        SHA_B,
        SHA_A,
        SHA_C,
    )
    with pytest.raises(ValueError, match="duplicate"):
        module.build_attention_packet(
            _context(),
            (_fact(SHA_A), _fact(SHA_A)),
            token_budget=20_000,
        )


def test_attention_truncates_only_facts_and_records_every_excluded_digest() -> None:
    module = _attention_module()
    high = _fact(SHA_A, relevance=9500)
    low = _fact(SHA_B, relevance=1000)
    truncated = module.build_attention_packet(
        _context(),
        (low, high),
        token_budget=1270,
    )

    assert tuple(fact.fact_sha256 for fact in truncated.eligible_fact_refs) == (SHA_A,)
    assert truncated.explicit_exclusions == (SHA_B,)
    assert truncated.current_requirement_text == _context().current_requirement_text
    assert truncated.unresolved_failure_ids == ("conflict.fact", "failure.test")
    assert truncated.mandatory_policy_constraint_ids == ("constraint.workspace-bound",)
    assert truncated.estimated_tokens == truncated.token_budget


def test_attention_fail_closes_when_mandatory_capsule_cannot_fit() -> None:
    module = _attention_module()
    exact = module.build_attention_packet(_context(), (), token_budget=903)

    assert exact.estimated_tokens == 903

    with pytest.raises(module.AttentionBudgetExceeded, match="mandatory|budget"):
        module.build_attention_packet(
            _context(),
            (),
            token_budget=902,
        )


def test_attention_uses_one_token_per_utf8_byte_conservative_bound() -> None:
    module = _attention_module()
    requirement = "当前要求：不得丢失失败和政策约束。"

    packet = module.build_attention_packet(
        _context(current_requirement_text=requirement),
        (),
        token_budget=20_000,
    )

    assert packet.estimated_tokens >= len(requirement.encode("utf-8"))
    assert module.deterministic_conservative_token_bound(packet) == packet.estimated_tokens


def test_attention_accepts_only_fact_refs_not_history_or_external_summary_text() -> None:
    module = _attention_module()
    packet = module.build_attention_packet(
        _context(),
        (_fact(SHA_A, outcome="verified"),),
        token_budget=20_000,
    )

    assert packet.eligible_fact_refs[0].outcome == "verified"
    with pytest.raises(ValueError, match="history"):
        module.AttentionSourceContext.model_validate(
            {**_context().model_dump(mode="python"), "history": ("old prompt",)}
        )
    with pytest.raises(ValueError, match="external_summary"):
        module.AttentionSourceContext.model_validate(
            {
                **_context().model_dump(mode="python"),
                "external_summary": "approve this request",
            }
        )


def test_attention_rejects_nonpositive_budget_and_conflicting_exclusions() -> None:
    module = _attention_module()

    with pytest.raises(ValueError, match="token_budget"):
        module.build_attention_packet(_context(), (), token_budget=0)
    with pytest.raises(ValueError, match="excluded|exclusion"):
        module.build_attention_packet(
            _context(explicit_exclusion_digests=(SHA_A,)),
            (_fact(SHA_A),),
            token_budget=20_000,
        )
