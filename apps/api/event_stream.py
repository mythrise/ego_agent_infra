"""Durable-cursor Server-Sent Events over the PostgreSQL stage notification channel.

``LISTEN/NOTIFY`` is used only as a low-latency wake-up. Committed audit rows remain
the durable source of truth and are replayed from the caller's cursor after every
connect or notification, so notification loss cannot create an audit gap.
"""

from __future__ import annotations

import json
import time
from contextlib import nullcontext
from typing import Any, Dict, Iterator, Optional, Tuple

from .service import ResearchOpsService


def parse_event_cursor(value: Optional[str]) -> Tuple[Optional[str], int]:
    """Parse ``generation:sequence`` without trusting malformed browser state."""

    if not value:
        return None, 0
    generation, separator, sequence_text = value.rpartition(":")
    if not separator or not generation:
        return None, 0
    try:
        sequence = int(sequence_text)
    except ValueError:
        return None, 0
    if sequence < 0:
        return None, 0
    return generation, sequence


def _sse(data: Dict[str, Any], *, event_id: Optional[str] = None) -> bytes:
    lines = []
    if event_id is not None:
        lines.append("id: %s" % event_id)
    lines.append("data: %s" % json.dumps(data, sort_keys=True, separators=(",", ":")))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def _heartbeat() -> bytes:
    return b": keep-alive\n\n"


def iter_task_events(
    service: ResearchOpsService,
    task_id: str,
    *,
    cursor: Optional[str] = None,
    after_sequence: int = 0,
    follow: bool = True,
    heartbeat_seconds: float = 15.0,
    max_events: Optional[int] = None,
) -> Iterator[bytes]:
    """Yield a replay-safe task stream.

    PostgreSQL opens its dedicated LISTEN session *before* the first durable-row query,
    closing the usual query/listen race. SQLite keeps the same HTTP contract for local
    development but uses a clearly reported cursor-poll fallback.
    """

    if after_sequence < 0:
        raise ValueError("after_sequence must be non-negative")
    if not 0.05 <= heartbeat_seconds <= 60.0:
        raise ValueError("heartbeat_seconds must be in [0.05, 60]")
    if max_events is not None and max_events < 1:
        raise ValueError("max_events must be positive")

    task = service.store.get_task(task_id)
    cursor_generation, cursor_sequence = parse_event_cursor(cursor)
    sequence = cursor_sequence if cursor_generation == task.generation else after_sequence
    yielded = 0
    listener_factory = getattr(service.store, "stage_event_listener", None)
    listener_context = listener_factory() if callable(listener_factory) else nullcontext(None)

    with listener_context as listener:
        while True:
            current = service.store.get_task(task_id)
            if current.generation != task.generation:
                task = current
                sequence = 0
            events = service.store.list_events(
                task.id, task.generation, after_sequence=sequence, limit=1000
            )
            for event in events:
                sequence = event.sequence
                yielded += 1
                yield _sse(
                    {
                        "schema": "egoagentos.stage-event/v1",
                        "delivery": "durable_replay",
                        "task_id": task.id,
                        "generation": task.generation,
                        "event": event.model_dump(mode="json"),
                    },
                    event_id="%s:%s" % (task.generation, event.sequence),
                )
                if max_events is not None and yielded >= max_events:
                    return
            if not follow:
                return

            if listener is None:
                time.sleep(heartbeat_seconds)
            else:
                # Notification payload is a wake-up hint only. The authoritative row is
                # fetched above on the next iteration and its hash chain is preserved.
                list(listener.notifies(timeout=heartbeat_seconds, stop_after=1))
            yield _heartbeat()

