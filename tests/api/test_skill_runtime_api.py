from fastapi.testclient import TestClient

from apps.api.main import create_app
from tests.api.operator_auth_helpers import (
    TEST_AUTHORIZATION_HEADERS,
    TEST_OPERATOR_ID,
    TEST_OPERATOR_KEY,
)


DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _client(database: str) -> TestClient:
    client = TestClient(
        create_app(
            database,
            operator_key=TEST_OPERATOR_KEY,
            operator_id=TEST_OPERATOR_ID,
        )
    )
    client.headers.update(TEST_AUTHORIZATION_HEADERS)
    return client


def _plan_payload() -> dict:
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


def test_skill_catalog_distinguishes_discovery_from_execution(tmp_path) -> None:
    client = _client(str(tmp_path / "catalog.sqlite3"))
    response = client.get("/api/v1/skills")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 6
    assert payload["executable"] == 3
    safe_runner = next(
        item for item in payload["items"] if item["name"] == "safe-experiment-runner"
    )
    assert safe_runner["executable"] is False


def test_skill_invoke_and_trace_are_correlated_and_digest_pinned(tmp_path) -> None:
    client = _client(str(tmp_path / "invoke.sqlite3"))
    catalog = client.get("/api/v1/skills").json()["items"]
    plan = next(item for item in catalog if item["name"] == "research-plan")
    body = {
        "correlation_id": "task_api_skill_123",
        "expected_version": plan["version"],
        "expected_package_digest": plan["package_digest"],
        "payload": _plan_payload(),
    }
    first = client.post("/api/v1/skills/research-plan/invoke", json=body)
    second = client.post("/api/v1/skills/research-plan/invoke", json=body)
    assert first.status_code == 200
    assert first.json() == second.json()
    trace = first.json()["trace"]
    fetched = client.get("/api/v1/skill-invocations/%s" % trace["invocation_id"])
    assert fetched.status_code == 200
    assert fetched.json() == trace


def test_generic_safe_runner_invocation_fails_closed_with_a_trace(tmp_path) -> None:
    client = _client(str(tmp_path / "deny.sqlite3"))
    response = client.post(
        "/api/v1/skills/safe-experiment-runner/invoke",
        json={"correlation_id": "task_deny", "payload": {}},
    )
    assert response.status_code == 403
    error = response.json()["error"]
    assert error["code"] == "skill_e_not_executable"
    assert error["details"]["trace"]["status"] == "FAIL"
