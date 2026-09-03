import re
from pathlib import Path
from typing import Any, Dict, Mapping

from fastapi.testclient import TestClient

from apps.api.main import create_app
from apps.api.expert_runs import ROLE_CONTRACTS, ExpertRunService, _system_prompt
from apps.api.research_os.service import ResearchOSService
from integrations.agentteams.model_gateway import ModelCall, ModelGatewayError
from tests.api.operator_auth_helpers import (
    TEST_AUTHORIZATION_HEADERS,
    TEST_OPERATOR_ID,
    TEST_OPERATOR_KEY,
)


class FakeLiveGateway:
    model = "test-live-model"
    base_url = "https://model.invalid/v1"

    def __init__(self) -> None:
        self.max_tokens_by_role: Dict[str, int] = {}

    def list_models(self) -> list[str]:
        return [self.model]

    def complete_json(
        self,
        *,
        role: str,
        system_prompt: str,
        input_payload: Mapping[str, Any],
        max_tokens: int,
    ) -> ModelCall:
        self.max_tokens_by_role[role] = max_tokens
        digest_match = re.search(r"input_digest MUST equal ([0-9a-f]{64})", system_prompt)
        review_match = re.search(r"reviewed_digest MUST equal ([0-9a-f]{64})", system_prompt)
        input_digest = digest_match.group(1) if digest_match else ""
        outputs: Dict[str, Dict[str, Any]] = {
            "research-pi": {
                "role": role,
                "input_digest": input_digest,
                "normalized_title": "Bounded root correction",
                "normalized_objective": "Test one bounded correction against a frozen baseline.",
                "assumptions": ["The supplied baseline is frozen."],
                "success_criteria": ["Report held-out primary metric."],
            },
            "scout": {
                "role": role,
                "input_digest": input_digest,
                "baseline_summary": "The supplied baseline is treated as unverified input.",
                "constraints": ["No test-set tuning."],
                "uncertainties": ["Repository contents were not retrieved."],
                "evidence_needs": ["Frozen split manifest."],
            },
            "experiment-architect": {
                "role": role,
                "input_digest": input_digest,
                "candidate_branches": ["identity control", "bounded residual"],
                "metrics": ["primary_metric", "runtime_seconds"],
                "folds": [0, 1],
                "seeds": [17, 23],
                "falsification_checks": ["shuffled-observation negative control"],
                "budget_assessment": "Resource plan remains required before execution.",
                "recommendation": "Compile the matrix, then request human review.",
            },
            "reviewer": {
                "role": role,
                "independent": True,
                "reviewed_digest": review_match.group(1) if review_match else "",
                "verdict": "WARN",
                "findings": ["No GPU receipt exists; planning only."],
                "decision": "Send the compiled plan to human review without execution.",
                "claim_boundary": "Live model planning and local compilation only.",
            },
        }
        output = outputs[role]
        return ModelCall(
            output=output,
            receipt={
                "schema": "egoagentos.model-gateway-receipt/v1",
                "truth_boundary": "LIVE_MODEL_RESPONSE_ONLY",
                "role": role,
                "model": self.model,
                "http_status": 200,
                "request_sha256": ("a" if role != "reviewer" else "c") * 64,
                "response_sha256": ("b" if role != "reviewer" else "d") * 64,
                "latency_ms": 12,
                "usage": {"total_tokens": 42},
            },
        )


class TerminalFailureGateway(FakeLiveGateway):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def complete_json(self, **kwargs: Any) -> ModelCall:
        self.calls += 1
        raise ModelGatewayError(
            "model gateway returned HTTP 403 (insufficient_user_quota)", retryable=False
        )


def _client(tmp_path: Path) -> TestClient:
    application = create_app(
        str(tmp_path / "researchops.sqlite3"),
        operator_key=TEST_OPERATOR_KEY,
        operator_id=TEST_OPERATOR_ID,
        expert_gateway=FakeLiveGateway(),
        expert_run_root=tmp_path / "artifacts",
    )
    return TestClient(application)


def test_role_prompts_require_an_exact_json_shape_without_extra_fields() -> None:
    digest = "a" * 64
    reviewed_digest = "b" * 64
    for role, contract in ROLE_CONTRACTS.items():
        prompt = _system_prompt(
            role,
            input_digest=digest,
            locale="en",
            reviewed_digest=reviewed_digest if role == "reviewer" else None,
        )

        assert "Use exactly these fields and no others" in prompt
        assert "never adding, renaming, or nesting fields" in prompt
        for field in contract["required"]:
            assert '"%s":' % field in prompt


def test_live_expert_run_calls_all_roles_and_freezes_auditable_result(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/expert-runs",
            headers=TEST_AUTHORIZATION_HEADERS,
            json={
                "input_mode": "idea",
                "locale": "en",
                "content": (
                    "Frozen baseline metric is 41.2. Explore a bounded residual without "
                    "test-set tuning and report runtime."
                ),
            },
        )
        assert response.status_code == 202
        run = client.get(
            "/api/v1/expert-runs/%s" % response.json()["run_id"],
            headers=TEST_AUTHORIZATION_HEADERS,
        ).json()

    assert run["status"] == "completed"
    assert [item["role"] for item in run["roles"]] == [
        "research-pi",
        "scout",
        "experiment-architect",
        "reviewer",
    ]
    assert all(item["status"] == "completed" for item in run["roles"])
    assert run["roles"][0]["context_receipt"]["upstream_roles"] == []
    assert run["roles"][2]["context_receipt"]["upstream_roles"] == [
        "research-pi",
        "scout",
    ]
    assert run["roles"][3]["context_receipt"]["upstream_roles"] == [
        "research-pi",
        "scout",
        "experiment-architect",
    ]
    assert all(
        len(item["context_receipt"]["payload_sha256"]) == 64 for item in run["roles"]
    )
    assert all(item["receipt"]["http_status"] == 200 for item in run["roles"])
    assert all(item["memory_receipt"]["compacted"] is True for item in run["roles"])
    assert client.app.state.expert_runs.gateway.max_tokens_by_role == {
        "research-pi": 2400,
        "scout": 2400,
        "experiment-architect": 4096,
        "reviewer": 4096,
    }
    assert run["compile"]["matrix_cell_count"] > 0
    assert run["decision"] == {
        "status": "PLAN_READY_FOR_HUMAN_REVIEW",
        "reviewer_verdict": "WARN",
        "reviewed_digest": run["roles"][-1]["output"]["reviewed_digest"],
        "execution_started": False,
    }
    assert run["truth_boundary"]["physical_gpu"] == "NOT_RUN"
    assert run["events"][-1]["event_hash"] == run["event_chain_sha256"]
    assert run["event_chain_valid"] is True
    assert "API_KEY" not in str(run)
    assert (tmp_path / "artifacts" / "expert-runs" / run["run_id"] / "run.json").is_file()


def test_expert_run_requires_operator_authentication(tmp_path: Path) -> None:
    with _client(tmp_path) as client:
        response = client.post(
            "/api/v1/expert-runs",
            json={"input_mode": "baseline", "locale": "en", "content": "x" * 50},
        )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "operator_auth_required"


def test_unconfigured_gateway_fails_closed(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.delenv("EGO_AGENT_MODEL_BASE_URL", raising=False)
    monkeypatch.delenv("EGO_AGENT_MODEL_API_KEY", raising=False)
    application = create_app(
        str(tmp_path / "researchops.sqlite3"),
        operator_key=TEST_OPERATOR_KEY,
        operator_id=TEST_OPERATOR_ID,
        expert_run_root=tmp_path / "artifacts",
    )
    with TestClient(application) as client:
        status = client.get("/api/v1/expert-runs/status")
        blocked = client.post(
            "/api/v1/expert-runs",
            headers=TEST_AUTHORIZATION_HEADERS,
            json={"input_mode": "detailed", "locale": "en", "content": "x" * 50},
        )
    assert status.json()["configured"] is False
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "expert_model_not_configured"


def test_environment_uses_low_reasoning_json_mode_for_live_experts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setenv("EGO_AGENT_MODEL_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("EGO_AGENT_MODEL_API_KEY", "server-only-key")
    monkeypatch.setenv("EGO_AGENT_MODEL", "agnes-2.5-pro")
    monkeypatch.delenv("EGO_AGENT_MODEL_REASONING_EFFORT", raising=False)
    research_os = ResearchOSService(memory_root=tmp_path / "research-os")

    service = ExpertRunService.from_environment(research_os, tmp_path / "artifacts")

    assert service.status()["reasoning_effort"] == "low"
    assert service.status()["structured_output"] == "json_object"


def test_terminal_gateway_error_is_not_retried(tmp_path: Path) -> None:
    gateway = TerminalFailureGateway()
    application = create_app(
        str(tmp_path / "researchops.sqlite3"),
        operator_key=TEST_OPERATOR_KEY,
        operator_id=TEST_OPERATOR_ID,
        expert_gateway=gateway,
        expert_run_root=tmp_path / "artifacts",
    )

    with TestClient(application) as client:
        response = client.post(
            "/api/v1/expert-runs",
            headers=TEST_AUTHORIZATION_HEADERS,
            json={"input_mode": "idea", "locale": "en", "content": "x" * 50},
        )
        run = client.get(
            "/api/v1/expert-runs/%s" % response.json()["run_id"],
            headers=TEST_AUTHORIZATION_HEADERS,
        ).json()

    assert gateway.calls == 1
    assert run["status"] == "failed"
    assert "insufficient_user_quota" in run["decision"]["error"]
    assert "after 1 attempt(s)" in run["decision"]["error"]
