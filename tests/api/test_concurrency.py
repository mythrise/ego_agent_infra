import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

from fastapi.testclient import TestClient

from apps.api.errors import ControlPlaneError
from apps.api.main import create_app
from apps.api.models import EvidenceKind, Stage
from apps.api.service import ResearchOpsService
from apps.api.store import SQLiteStore
from tests.api.operator_auth_helpers import (
    TEST_AUTHORIZATION_HEADERS,
    TEST_OPERATOR_ID,
    TEST_OPERATOR_KEY,
)


TASK_ID = "ego-lite-001"


def _start_together(count: int = 2) -> tuple[threading.Barrier, ThreadPoolExecutor]:
    return threading.Barrier(count), ThreadPoolExecutor(max_workers=count)


def test_two_services_serialize_transition_and_keep_audit_chain_linear(tmp_path: Path) -> None:
    database = str(tmp_path / "two-services.sqlite3")
    first = ResearchOpsService(SQLiteStore(database))
    second = ResearchOpsService(SQLiteStore(database))

    # Widen the stale-read window without coordinating from inside the mutation section. The
    # regression remains a real concurrent call through two independent service/store objects.
    for service in (first, second):
        original: Callable[[str], Any] = service.store.get_task

        def delayed_get(task_id: str, read: Callable[[str], Any] = original) -> Any:
            task = read(task_id)
            time.sleep(0.05)
            return task

        service.store.get_task = delayed_get  # type: ignore[method-assign]

    barrier, executor = _start_together()

    def advance(service: ResearchOpsService) -> tuple[str, Any]:
        barrier.wait()
        try:
            return "ok", service.advance(TASK_ID, Stage.CONTEXT)
        except ControlPlaneError as error:
            return "error", error.code

    with executor:
        results = list(executor.map(advance, (first, second)))

    assert sorted(result[0] for result in results) == ["error", "ok"]
    assert [result[1] for result in results if result[0] == "error"] == ["illegal_transition"]
    task = first.store.get_task(TASK_ID)
    evidence = first.store.list_evidence(TASK_ID, task.generation)
    transitions = [
        event
        for event in first.store.list_events(TASK_ID, task.generation, limit=1000)
        if event.event_type == "state.transitioned"
        and event.payload.get("from") == "INTAKE"
        and event.payload.get("to") == "CONTEXT"
    ]
    assert sum(record.kind == EvidenceKind.DATASET_MANIFEST for record in evidence) == 1
    assert len(transitions) == 1
    assert first.store.verify_event_chain(TASK_ID, task.generation) is True


def test_two_testclients_execute_same_idempotency_key_only_once(tmp_path: Path) -> None:
    database = tmp_path / "two-clients.sqlite3"
    first_app = create_app(
        str(database), operator_key=TEST_OPERATOR_KEY, operator_id=TEST_OPERATOR_ID
    )
    second_app = create_app(
        str(database), operator_key=TEST_OPERATOR_KEY, operator_id=TEST_OPERATOR_ID
    )
    original_first = first_app.state.service.reset_demo
    original_second = second_app.state.service.reset_demo

    def delayed_reset_first(scenario: str = "happy_path") -> dict[str, Any]:
        time.sleep(0.05)
        return original_first(scenario)

    def delayed_reset_second(scenario: str = "happy_path") -> dict[str, Any]:
        time.sleep(0.05)
        return original_second(scenario)

    first_app.state.service.reset_demo = delayed_reset_first
    second_app.state.service.reset_demo = delayed_reset_second
    barrier, executor = _start_together()

    with TestClient(first_app) as first_client, TestClient(second_app) as second_client:
        first_client.headers.update(TEST_AUTHORIZATION_HEADERS)
        second_client.headers.update(TEST_AUTHORIZATION_HEADERS)

        def reset(client: TestClient) -> dict[str, Any]:
            barrier.wait()
            response = client.post(
                "/api/v1/demo/reset",
                json={"scenario": "happy_path"},
                headers={"Idempotency-Key": "concurrent-reset-key"},
            )
            assert response.status_code == 200, response.text
            return response.json()

        with executor:
            responses = list(executor.map(reset, (first_client, second_client)))

    assert responses[0]["task"]["generation"] == responses[1]["task"]["generation"]
    assert sum(bool(response.get("idempotent_replay")) for response in responses) == 1
    with sqlite3.connect(database) as connection:
        reset_events = connection.execute(
            "SELECT COUNT(*) FROM audit_events WHERE event_type='demo.reset'"
        ).fetchone()[0]
        generations = connection.execute("SELECT generation FROM tasks WHERE id=?", (TASK_ID,)).fetchone()
    # One initialization plus exactly one idempotent reset operation.
    assert reset_events == 2
    assert generations is not None and generations[0] == responses[0]["task"]["generation"]
