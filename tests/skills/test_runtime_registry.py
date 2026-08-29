from pathlib import Path

import pytest

from skill_runtime import SkillInvocationError, SkillRegistry, default_handlers


ROOT = Path(__file__).resolve().parents[2]
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def registry() -> SkillRegistry:
    return SkillRegistry.discover(ROOT / "skills", default_handlers())


def plan_payload() -> dict:
    return {
        "goal_frozen": True,
        "goal_digest": DIGEST_A,
        "context_digest": DIGEST_B,
        "hypotheses": ["candidate improves throughput without exceeding error budget"],
        "arms": ["baseline", "candidate"],
        "seeds": [11, 22, 33],
        "estimated_gpu_hours": 2.0,
        "budget_gpu_hours": 3.0,
        "rollback_target": "git:abc123",
        "metrics": [
            {
                "name": "throughput",
                "direction": "higher_better",
                "unit": "fps",
                "threshold": 10,
                "split": "test-v1",
                "aggregation": "mean",
            }
        ],
    }


def test_discovers_six_digest_pinned_packages_and_three_executable_handlers() -> None:
    catalog = registry().catalog()
    assert len(catalog) == 6
    assert sum(item["executable"] for item in catalog) == 3
    assert all(len(item["package_digest"]) == 64 for item in catalog)
    assert all(item["owner_agent"] != item["reviewer_agent"] for item in catalog)


def test_invoke_is_idempotently_correlated_and_emits_digest_trace() -> None:
    runtime = registry()
    first = runtime.invoke("research-plan", plan_payload(), "task_123")
    second = runtime.invoke("research-plan", plan_payload(), "task_123")
    assert first == second
    assert first["result"]["status"] == "READY_FOR_INDEPENDENT_REVIEW"
    assert first["trace"]["status"] == "PASS"
    assert runtime.trace(first["trace"]["invocation_id"]) == first["trace"]


def test_version_or_package_digest_mismatch_fails_closed_with_trace() -> None:
    runtime = registry()
    with pytest.raises(SkillInvocationError) as caught:
        runtime.invoke(
            "research-plan",
            plan_payload(),
            "task_123",
            expected_version="9.9.9",
        )
    assert caught.value.code == "E_VERSION_PIN"
    assert caught.value.trace.status == "FAIL"
    assert caught.value.trace.output_digest is None


def test_discoverable_non_allowlisted_skill_cannot_execute() -> None:
    runtime = registry()
    with pytest.raises(SkillInvocationError) as caught:
        runtime.invoke("safe-experiment-runner", {}, "task_123")
    assert caught.value.code == "E_NOT_EXECUTABLE"


def test_evidence_gate_requires_independence_and_complete_evidence() -> None:
    runtime = registry()
    passing = runtime.invoke(
        "evidence-gate",
        {
            "required_kinds": ["metric", "trace", "review"],
            "evidence": [
                {"kind": "metric", "digest": DIGEST_A, "producer": "evaluator"},
                {"kind": "trace", "digest": DIGEST_B, "producer": "runtime"},
                {"kind": "review", "digest": "c" * 64, "producer": "reviewer"},
            ],
            "executor": "runtime",
            "reviewer": "reviewer",
        },
        "task_gate",
    )
    assert passing["result"]["status"] == "PASS"
    with pytest.raises(SkillInvocationError) as caught:
        runtime.invoke(
            "evidence-gate",
            {
                "required_kinds": ["metric", "trace"],
                "evidence": [{"kind": "metric", "digest": DIGEST_A}],
                "executor": "runtime",
                "reviewer": "runtime",
            },
            "task_gate_bad",
        )
    assert caught.value.code == "E_INPUT"


def test_canary_routing_is_stable_and_rollback_reactivates_target() -> None:
    runtime = registry()
    # The repository carries one version per package, so lifecycle semantics are
    # exercised on that release: draft -> canary -> active -> retired -> rollback.
    canary = runtime.set_canary("research-plan", "0.1.0", 25)
    assert canary["state"] == "canary"
    assert runtime.resolve("research-plan", "same-correlation").version == "0.1.0"
    active = runtime.activate("research-plan", "0.1.0")
    assert active["state"] == "active"
    runtime.retire("research-plan", "0.1.0")
    rolled_back = runtime.rollback("research-plan", "0.1.0")
    assert rolled_back["action"] == "rollback"
    assert rolled_back["state"] == "active"
