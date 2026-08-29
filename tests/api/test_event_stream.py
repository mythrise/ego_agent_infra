from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from apps.api.event_stream import iter_task_events, parse_event_cursor
from apps.api.service import DEMO_TASK_ID


def decode_event(chunk: bytes) -> dict[str, Any]:
    data_line = next(line for line in chunk.decode("utf-8").splitlines() if line.startswith("data: "))
    return json.loads(data_line.removeprefix("data: "))


def test_cursor_parser_fails_closed() -> None:
    assert parse_event_cursor(None) == (None, 0)
    assert parse_event_cursor("") == (None, 0)
    assert parse_event_cursor("bad") == (None, 0)
    assert parse_event_cursor("generation:not-an-int") == (None, 0)
    assert parse_event_cursor("generation:-1") == (None, 0)
    assert parse_event_cursor("gen_a:12") == ("gen_a", 12)


def test_non_follow_stream_replays_hash_chained_events(client: TestClient) -> None:
    service: Any = client.app.state.service
    task = service.store.get_task(DEMO_TASK_ID)
    chunks = list(iter_task_events(service, DEMO_TASK_ID, follow=False))
    payloads = [decode_event(chunk) for chunk in chunks]

    assert payloads
    assert all(payload["delivery"] == "durable_replay" for payload in payloads)
    assert [payload["event"]["sequence"] for payload in payloads] == list(
        range(1, len(payloads) + 1)
    )
    assert all(payload["generation"] == task.generation for payload in payloads)

    resumed = list(
        iter_task_events(
            service,
            DEMO_TASK_ID,
            cursor=f"{task.generation}:{len(payloads) - 1}",
            follow=False,
        )
    )
    assert len(resumed) == 1
    assert decode_event(resumed[0])["event"]["sequence"] == len(payloads)


def test_stale_generation_cursor_replays_current_generation(client: TestClient) -> None:
    service: Any = client.app.state.service
    chunks = list(
        iter_task_events(service, DEMO_TASK_ID, cursor="gen_stale:999", follow=False)
    )
    assert chunks
    assert decode_event(chunks[0])["event"]["sequence"] == 1


def test_http_sse_contract_and_missing_task(client: TestClient) -> None:
    response = client.get(f"/api/v1/tasks/{DEMO_TASK_ID}/event-stream?follow=false")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-ego-event-mode"] == "sqlite-cursor-fallback"
    assert "egoagentos.stage-event/v1" in response.text

    missing = client.get("/api/v1/tasks/no-such-task/event-stream?follow=false")
    assert missing.status_code == 404
