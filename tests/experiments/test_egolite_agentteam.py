from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from experiments.egolite_agentteam.run import _normalize_reviewer_verdict, run_acceptance
from experiments.egolite_agentteam.verify import verify_bundle
from integrations.agentteams.model_gateway import ModelCall


class FakeGateway:
    base_url = "https://gateway.example/v1"
    model = "fixture-model"

    def complete_json(
        self,
        *,
        role: str,
        system_prompt: str,
        input_payload: Mapping[str, Any],
        max_tokens: int = 900,
    ) -> ModelCall:
        expected: Dict[str, Dict[str, Any]] = {
            "research-pi": {
                "role": role,
                "objective_digest": input_payload["goal_digest"]
                if "goal_digest" in input_payload
                else "",
                "stages": ["PLAN", "APPROVAL", "EXECUTE", "VERIFY", "DECIDE"],
                "approval_required": True,
            },
            "scout": {
                "role": role,
                "input_digest": "",
                "constraints": ["synthetic workload"],
                "uncertainties": ["no physical GPU"],
            },
            "experiment-architect": {
                "role": role,
                "plan_digest": "",
                "falsification_checks": ["fps", "mpjpe"],
                "budget_assessment": "bounded",
                "recommendation": "proceed after approval",
            },
            "reviewer": {
                "role": role,
                "independent": True,
                "reviewed_evidence_digest": "",
                "verdict": "PASS",
                "findings": ["boundary preserved"],
                "claim_boundary": "synthetic metrics only",
            },
        }
        # Exact digest bindings are present in the system prompt. Extract the one required
        # value so this fake tests the same correlation validation as a live provider.
        for key in ("objective_digest", "input_digest", "plan_digest", "reviewed_evidence_digest"):
            marker = '"%s": "' % key
            if marker in system_prompt:
                expected[role][key] = system_prompt.split(marker, 1)[1].split('"', 1)[0]
        return ModelCall(
            output=expected[role],
            receipt={
                "schema": "egoagentos.model-gateway-receipt/v1",
                "role": role,
                "http_status": 200,
                "request_sha256": "1" * 64,
                "response_sha256": "2" * 64,
            },
        )


def test_reviewer_verdict_normalization_retains_provider_value() -> None:
    normalized = _normalize_reviewer_verdict({"verdict": "PASS_WITH_BOUNDARY"})
    assert normalized["verdict"] == "WARN"
    assert normalized["provider_verdict"] == "PASS_WITH_BOUNDARY"
    assert "deterministic" in normalized["verdict_normalization"]
    rejected = _normalize_reviewer_verdict({"verdict": "REJECT"})
    assert rejected["verdict"] == "FAIL"
    assert rejected["provider_verdict"] == "REJECT"


def test_bounded_model_team_acceptance_bundle(tmp_path: Path) -> None:
    workspace = Path(__file__).resolve().parents[2]
    output = tmp_path / "acceptance"
    result = run_acceptance(FakeGateway(), workspace=workspace, output_dir=output)  # type: ignore[arg-type]
    assert result["structural_acceptance"] == "PASS"
    assert result["truth_boundary"] == {
        "external_model_calls": "LIVE",
        "control_plane_replay": "LIVE_LOCAL",
        "ego_workload_metrics": "SYNTHETIC_FIXTURE",
        "official_agentteams_controller": "NOT_RUN",
        "matrix_transport": "NOT_RUN",
        "physical_gpu": "NOT_RUN",
    }
    assert result["output"]["distinct_roles"] == [
        "experiment-architect",
        "research-pi",
        "reviewer",
        "scout",
    ]
    assert (output / "acceptance.json").is_file()
    assert (output / "SHA256SUMS.json").is_file()
    control = (output / "control-plane.json").read_text(encoding="utf-8")
    assert "egoap_" not in control
    assert "user-authorized-session-test" in control
    verification = verify_bundle(output)
    assert verification["verified"] is True
    assert verification["errors"] == []
