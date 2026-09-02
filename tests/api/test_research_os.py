from pathlib import Path

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.research_os.agent_memory import AgentMemoryRegistry
from apps.api.research_os.ladder import compile_research_input
from apps.api.research_os.models import (
    FocusMessage,
    InputTier,
    ResearchInput,
    ResourcePlan,
    StageCommitRequest,
)
from apps.api.research_os.resource_reviewer import IndependentResourceReviewer
from apps.api.research_os.service import ResearchOSService
from apps.api.research_os.tencent_memory import TencentAgentMemoryAdapter
from tests.api.operator_auth_helpers import TEST_AUTHORIZATION_HEADERS, TEST_OPERATOR_KEY


def _research_input(**overrides):
    values = {
        "title": "World model root recovery",
        "objective": "Reduce held-out global root translation error",
        "baseline": "Frozen G5 baseline with five OOF folds",
        "folds": [0, 1],
        "seeds": [17],
    }
    values.update(overrides)
    return ResearchInput(**values)


def _good_resource_plan(**overrides):
    values = {
        "matrix_cells": 12,
        "folds": 5,
        "estimated_cpu_hours": 24,
        "estimated_gpu_hours": 4,
        "cpu_per_cell": 4,
        "gpu_per_cell": 1,
        "row_shards": 8,
        "checkpoint_interval_minutes": 20,
        "resume_supported": True,
        "shared_dataset_cache": True,
        "recomputes_fold_invariant_data": False,
        "concurrent_cells": 4,
        "global_phase_barrier": False,
        "validation_coupled_to_compute": False,
        "output_partition_key": "cell_id/row_shard",
    }
    values.update(overrides)
    return ResourcePlan(**values)


def _stage(stage_id: str, task_id: str = "task-1") -> StageCommitRequest:
    return StageCommitRequest(
        team_id="team-ego",
        user_id="mythrise",
        session_id="session-1",
        task_id=task_id,
        stage_id=stage_id,
        messages=[
            FocusMessage(role="user", content="Try a bounded SE(3) residual."),
            FocusMessage(role="assistant", content="Freeze OOF predictions before fitting."),
        ],
        decisions=["Use a bounded residual in tangent space."],
        evidence=["Five fold identities are frozen."],
        blockers=["No live GPU receipt yet."],
        next_actions=["Run fold-sharded smoke test."],
        validated_facts=["G5 is the comparison baseline."],
    )


def test_capability_ladder_is_deterministic_and_expands_to_cells() -> None:
    baseline_only = compile_research_input(_research_input())
    again = compile_research_input(_research_input())
    assert baseline_only == again
    assert baseline_only["normalized_proposal"]["tier"] == InputTier.BASELINE_ONLY.value
    assert baseline_only["normalized_proposal"]["normalization"]["model_call"] == "NOT_RUN"
    assert baseline_only["tree"]["children"][0]["kind"] == "baseline"
    assert baseline_only["matrix"]["cell_count"] > 20
    assert all(cell["intent_token"].startswith("rxpi_") for cell in baseline_only["matrix"]["cells"])

    fuzzy = compile_research_input(_research_input(idea="Learn head-camera translation."))
    assert fuzzy["normalized_proposal"]["tier"] == InputTier.FUZZY.value

    detailed = compile_research_input(
        _research_input(
            proposal="Compare analytic and learned translation with frozen folds.",
            branches=["B0", "B1"],
            core_code="def residual(x): return x",
        )
    )
    assert detailed["normalized_proposal"]["tier"] == InputTier.DETAILED.value
    assert detailed["normalized_proposal"]["normalization"]["method"] == "user_specified"


def test_independent_resource_veto_survives_human_approval() -> None:
    unsafe = _good_resource_plan(
        cpu_per_cell=1,
        row_shards=1,
        checkpoint_interval_minutes=None,
        resume_supported=False,
        shared_dataset_cache=False,
        recomputes_fold_invariant_data=True,
        concurrent_cells=1,
        global_phase_barrier=True,
        validation_coupled_to_compute=True,
        output_partition_key="fold/output",
        human_approved=True,
    )
    result = IndependentResourceReviewer().review(unsafe)
    codes = {finding["code"] for finding in result["findings"]}
    assert result["decision"] == "VETO"
    assert result["human_approval_observed"] is True
    assert result["human_approval_can_override"] is False
    assert {
        "FOLD_INVARIANT_RECOMPUTE",
        "CELL_GRAIN_TOO_COARSE",
        "NO_ROW_SHARDS",
        "NO_CHECKPOINT_RESUME",
        "OUTPUT_COLLISION_RISK",
        "UNNECESSARY_GLOBAL_BARRIER",
        "VALIDATION_COMPUTE_COUPLED",
        "LOW_PARALLEL_UTILIZATION",
    }.issubset(codes)

    safe = IndependentResourceReviewer().review(_good_resource_plan())
    assert safe["decision"] == "PASS"
    assert safe["gate"] == "ALLOW_APPROVAL_GATE"

    mismatched = IndependentResourceReviewer().review(
        _good_resource_plan(), expected_matrix_cells=66, expected_folds=2
    )
    assert mismatched["decision"] == "VETO"
    assert {finding["code"] for finding in mismatched["findings"]} == {
        "MATRIX_CARDINALITY_MISMATCH",
        "FOLD_CARDINALITY_MISMATCH",
    }


def test_each_agent_gets_private_database_markdown_and_digest_chain(tmp_path: Path) -> None:
    registry = AgentMemoryRegistry(tmp_path)
    planner = registry.for_agent("planner")
    reviewer = registry.for_agent("reviewer")

    first = planner.commit(_stage("PLAN"))
    second = planner.commit(_stage("PLAN_REVIEW"))
    other = reviewer.commit(_stage("REVIEW", task_id="task-2"))

    assert first["physical_database"] != other["physical_database"]
    assert first["markdown_projection"] != other["markdown_projection"]
    assert second["previous_sha256"] == first["receipt_sha256"]
    assert Path(second["markdown_projection"]).read_text(encoding="utf-8").startswith(
        "# Agent Focus"
    )
    assert second["compacted"] is True
    assert planner.read()["markdown_sha256"] == second["markdown_sha256"]


def test_tencent_memory_adapter_uses_published_v3_skill_contract(monkeypatch) -> None:
    adapter = TencentAgentMemoryAdapter(
        "https://memory.example", "not-a-real-key", "service-1", space_id="space-1"
    )
    calls = []

    def fake_post(path, body):
        calls.append((path, body))
        return {"status": "ok"}

    monkeypatch.setattr(adapter, "_post", fake_post)
    result = adapter.commit_and_compact(
        team_id="team-1",
        agent_id="agent-1",
        user_id="user-1",
        session_id="session-1",
        task_id="task-1",
        stage_id="PLAN",
        messages=[FocusMessage(role="assistant", content="done")],
    )
    assert [item[0] for item in calls] == [
        "/v3/skill/conversation/add",
        "/v3/skill/conversation/force-archive",
    ]
    assert all(call[1]["space_id"] == "space-1" for call in calls)
    assert all(call[1]["agent_id"] == "agent-1" for call in calls)
    assert result["truth_class"] == "LIVE"


def test_research_os_routes_expose_truthful_storage_and_full_compile(tmp_path: Path) -> None:
    app = create_app(
        db_path=str(tmp_path / "control.sqlite3"),
        operator_key=TEST_OPERATOR_KEY,
        allow_unauthenticated_demo=False,
    )
    app.state.research_os = ResearchOSService(memory_root=tmp_path / "agents")
    client = TestClient(app)

    storage = client.get("/api/v1/research/storage")
    assert storage.status_code == 200
    assert storage.json()["authority_target"]["truth_class"] == "NOT_CONFIGURED"
    assert storage.json()["deterministic_fallback"]["truth_class"] == "LIVE_LOCAL"

    compiled = client.post(
        "/api/v1/research/compile",
        headers=TEST_AUTHORIZATION_HEADERS,
        json={
            "input": _research_input(idea="Calibrate translation").model_dump(mode="json"),
            "resource_plan": _good_resource_plan(matrix_cells=66, folds=2).model_dump(mode="json"),
        },
    )
    assert compiled.status_code == 200, compiled.text
    assert compiled.json()["resource_review"]["decision"] == "PASS"
    assert compiled.json()["resource_review"]["compiled_expectation"] == {
        "matrix_cells": 66,
        "folds": 2,
    }

    committed = client.post(
        "/api/v1/research/agents/planner/stages/commit",
        headers=TEST_AUTHORIZATION_HEADERS,
        json=_stage("PLAN").model_dump(mode="json"),
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["local"]["truth_class"] == "LIVE_LOCAL"
    assert committed.json()["remote"]["truth_class"] == "NOT_CONFIGURED"
