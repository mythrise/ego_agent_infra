"""SQLite persistence for tasks, approvals, evidence, memory, and hash-chained audit events."""

import fcntl
import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, BinaryIO, Dict, Iterator, List, Optional, Tuple

from .errors import ConflictError, NotFoundError
from .models import (
    ApprovalRecord,
    AuditEvent,
    EvidenceRecord,
    MemoryCandidate,
    MemoryRecord,
    Stage,
    TaskRecord,
    utc_now,
)
from .provenance import canonical_sha256


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    generation TEXT NOT NULL,
    version INTEGER NOT NULL,
    task_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    token_hash TEXT,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_approvals_task_generation
    ON approvals(task_id, generation, created_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_approvals_token_hash
    ON approvals(token_hash) WHERE token_hash IS NOT NULL;

CREATE TABLE IF NOT EXISTS evidence (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    kind TEXT NOT NULL,
    artifact_digest TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_task_generation
    ON evidence(task_id, generation, created_at);

CREATE TRIGGER IF NOT EXISTS evidence_no_update
BEFORE UPDATE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS evidence_no_delete
BEFORE DELETE ON evidence
BEGIN
    SELECT RAISE(ABORT, 'evidence ledger is immutable');
END;

CREATE TABLE IF NOT EXISTS memory_candidates (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    evidence_digest TEXT NOT NULL,
    review_id TEXT NOT NULL,
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memory_candidates_task_generation
    ON memory_candidates(task_id, generation, created_at);

CREATE TRIGGER IF NOT EXISTS memory_candidates_no_update
BEFORE UPDATE ON memory_candidates
BEGIN
    SELECT RAISE(ABORT, 'memory candidate ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS memory_candidates_no_delete
BEFORE DELETE ON memory_candidates
BEGIN
    SELECT RAISE(ABORT, 'memory candidate ledger is immutable');
END;

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    validated INTEGER NOT NULL CHECK(validated IN (0, 1)),
    record_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_memories_task_generation
    ON memories(task_id, generation, created_at);

CREATE TRIGGER IF NOT EXISTS memories_no_update
BEFORE UPDATE ON memories
BEGIN
    SELECT RAISE(ABORT, 'validated memory ledger is immutable');
END;

CREATE TRIGGER IF NOT EXISTS memories_no_delete
BEFORE DELETE ON memories
BEGIN
    SELECT RAISE(ABORT, 'validated memory ledger is immutable');
END;

CREATE TABLE IF NOT EXISTS audit_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    task_id TEXT NOT NULL,
    generation TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT NOT NULL,
    stage TEXT,
    payload_json TEXT NOT NULL,
    previous_hash TEXT NOT NULL,
    event_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_stream
    ON audit_events(task_id, generation, sequence);

CREATE TRIGGER IF NOT EXISTS audit_events_no_update
BEFORE UPDATE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END;

CREATE TRIGGER IF NOT EXISTS audit_events_no_delete
BEFORE DELETE ON audit_events
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END;

CREATE TABLE IF NOT EXISTS idempotency (
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    response_json TEXT NOT NULL,
    status_code INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY(method, path, key)
);
"""


class _MutationState:
    """Process-local side of a database-scoped, cross-process mutation lock."""

    def __init__(self, lock_path: Optional[Path]) -> None:
        self.lock_path = lock_path
        self.thread_lock = threading.RLock()
        self.local = threading.local()


_MUTATION_STATES_LOCK = threading.Lock()
_MUTATION_STATES: Dict[str, _MutationState] = {}


def _mutation_state(db_path: str) -> _MutationState:
    if db_path == ":memory:":
        return _MutationState(None)
    resolved = Path(db_path).expanduser().resolve()
    key = str(resolved)
    with _MUTATION_STATES_LOCK:
        state = _MUTATION_STATES.get(key)
        if state is None:
            state = _MutationState(Path("%s.mutation.lock" % resolved))
            _MUTATION_STATES[key] = state
        return state


class SQLiteStore:
    engine = "sqlite"
    audit_guarantee = "trigger_immutable_application_hash_chain"

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self.location = db_path
        self._lock = threading.RLock()
        self._mutation_state = _mutation_state(db_path)
        self._transaction_local = threading.local()
        if db_path != ":memory:":
            Path(db_path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        self._memory_connection: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._memory_connection = sqlite3.connect(":memory:", check_same_thread=False)
            self._memory_connection.row_factory = sqlite3.Row
        self.initialize()

    @contextmanager
    def mutation_lock(self) -> Iterator[None]:
        """Serialize one logical mutation across threads, stores, and POSIX processes.

        Lock order is always database-scoped process RLock, advisory file lock, then the
        store-local SQLite lock. Thread-local depth makes the first two levels reentrant.
        """

        state = self._mutation_state
        with state.thread_lock:
            depth = int(getattr(state.local, "depth", 0))
            handle: Optional[BinaryIO] = None
            if depth == 0 and state.lock_path is not None:
                handle = state.lock_path.open("a+b")
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                except Exception:
                    handle.close()
                    raise
                state.local.handle = handle
            state.local.depth = depth + 1
            try:
                yield
            finally:
                remaining = int(state.local.depth) - 1
                state.local.depth = remaining
                if remaining == 0 and state.lock_path is not None:
                    locked_handle: BinaryIO = state.local.handle
                    try:
                        fcntl.flock(locked_handle.fileno(), fcntl.LOCK_UN)
                    finally:
                        locked_handle.close()
                        del state.local.handle

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit one logical control-plane mutation atomically.

        Nested service/idempotency scopes share one SQLite connection. Store methods
        defer their local commit while this scope is active, so approval, task, evidence,
        memory, audit, and idempotency rows either all persist or all roll back.
        """

        with self.mutation_lock():
            depth = int(getattr(self._transaction_local, "depth", 0))
            if depth:
                self._transaction_local.depth = depth + 1
                try:
                    yield
                except Exception:
                    self._transaction_local.rollback_only = True
                    raise
                finally:
                    self._transaction_local.depth = depth
                return

            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._transaction_local.connection = connection
                self._transaction_local.depth = 1
                self._transaction_local.rollback_only = False
                try:
                    yield
                except Exception:
                    connection.rollback()
                    raise
                else:
                    if self._transaction_local.rollback_only:
                        connection.rollback()
                        raise RuntimeError("nested database mutation failed; transaction rolled back")
                    connection.commit()
            finally:
                self._transaction_local.depth = 0
                if hasattr(self._transaction_local, "connection"):
                    del self._transaction_local.connection
                if hasattr(self._transaction_local, "rollback_only"):
                    del self._transaction_local.rollback_only
                if connection is not self._memory_connection:
                    connection.close()

    def _active_transaction(self) -> Optional[sqlite3.Connection]:
        return getattr(self._transaction_local, "connection", None)

    def _connect(self) -> sqlite3.Connection:
        active = self._active_transaction()
        if active is not None:
            return active
        if self._memory_connection is not None:
            connection = self._memory_connection
        else:
            connection = sqlite3.connect(self.db_path, timeout=10.0)
            connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _close(self, connection: sqlite3.Connection) -> None:
        if connection is self._active_transaction():
            return
        if connection is not self._memory_connection:
            connection.close()

    def _commit(self, connection: sqlite3.Connection) -> None:
        if connection is not self._active_transaction():
            connection.commit()

    def _rollback(self, connection: sqlite3.Connection) -> None:
        if connection is not self._active_transaction():
            connection.rollback()

    def initialize(self) -> None:
        with self._lock:
            connection = self._connect()
            try:
                if self._memory_connection is None:
                    connection.execute("PRAGMA journal_mode = WAL")
                connection.executescript(SCHEMA)
                self._commit(connection)
            finally:
                self._close(connection)

    def ping(self) -> bool:
        with self._lock:
            connection = self._connect()
            try:
                return connection.execute("SELECT 1").fetchone()[0] == 1
            finally:
                self._close(connection)

    def upsert_seed_task(self, task: TaskRecord) -> None:
        serialized = task.model_dump_json()
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO tasks(id, generation, version, task_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        generation=excluded.generation,
                        version=excluded.version,
                        task_json=excluded.task_json,
                        created_at=excluded.created_at,
                        updated_at=excluded.updated_at
                    """,
                    (
                        task.id,
                        task.generation,
                        task.version,
                        serialized,
                        task.created_at.isoformat(),
                        task.updated_at.isoformat(),
                    ),
                )
                self._commit(connection)
            finally:
                self._close(connection)

    def create_task(self, task: TaskRecord) -> None:
        """Insert a user-owned task without the demo reset/upsert semantics."""

        serialized = task.model_dump_json()
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO tasks(id, generation, version, task_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        task.id,
                        task.generation,
                        task.version,
                        serialized,
                        task.created_at.isoformat(),
                        task.updated_at.isoformat(),
                    ),
                )
                self._commit(connection)
            except sqlite3.IntegrityError as error:
                self._rollback(connection)
                raise ConflictError(
                    "task_already_exists",
                    "A task with this id already exists; live task creation never overwrites it",
                    {"task_id": task.id},
                ) from error
            finally:
                self._close(connection)

    def save_task(self, task: TaskRecord, expected_version: int) -> None:
        with self._lock:
            connection = self._connect()
            try:
                cursor = connection.execute(
                    """
                    UPDATE tasks
                    SET generation=?, version=?, task_json=?, updated_at=?
                    WHERE id=? AND generation=? AND version=?
                    """,
                    (
                        task.generation,
                        task.version,
                        task.model_dump_json(),
                        task.updated_at.isoformat(),
                        task.id,
                        task.generation,
                        expected_version,
                    ),
                )
                if cursor.rowcount != 1:
                    self._rollback(connection)
                    raise ConflictError(
                        "task_version_conflict",
                        "Task changed concurrently; reload it before retrying",
                        {"task_id": task.id, "expected_version": expected_version},
                    )
                self._commit(connection)
            finally:
                self._close(connection)

    def get_task(self, task_id: str) -> TaskRecord:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT task_json FROM tasks WHERE id=?", (task_id,)
                ).fetchone()
            finally:
                self._close(connection)
        if row is None:
            raise NotFoundError("task", task_id)
        return TaskRecord.model_validate_json(row["task_json"])

    def list_tasks(self) -> List[TaskRecord]:
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    "SELECT task_json FROM tasks ORDER BY updated_at DESC, id"
                ).fetchall()
            finally:
                self._close(connection)
        return [TaskRecord.model_validate_json(row["task_json"]) for row in rows]

    def add_approval(self, approval: ApprovalRecord) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO approvals(
                        id, task_id, generation, status, expires_at, token_hash,
                        record_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval.id,
                        approval.task_id,
                        approval.generation,
                        approval.status.value,
                        approval.expires_at.isoformat(),
                        approval.token_hash,
                        approval.model_dump_json(),
                        approval.requested_at.isoformat(),
                    ),
                )
                self._commit(connection)
            finally:
                self._close(connection)

    def save_approval(self, approval: ApprovalRecord) -> None:
        with self._lock:
            connection = self._connect()
            try:
                cursor = connection.execute(
                    """
                    UPDATE approvals
                    SET status=?, expires_at=?, token_hash=?, record_json=?
                    WHERE id=?
                    """,
                    (
                        approval.status.value,
                        approval.expires_at.isoformat(),
                        approval.token_hash,
                        approval.model_dump_json(),
                        approval.id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise NotFoundError("approval", approval.id)
                self._commit(connection)
            finally:
                self._close(connection)

    @staticmethod
    def _approval_from_row(row: sqlite3.Row) -> ApprovalRecord:
        approval = ApprovalRecord.model_validate_json(row["record_json"])
        approval.token_hash = row["token_hash"]
        return approval

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT * FROM approvals WHERE id=?", (approval_id,)
                ).fetchone()
            finally:
                self._close(connection)
        if row is None:
            raise NotFoundError("approval", approval_id)
        return self._approval_from_row(row)

    def latest_approval(self, task_id: str, generation: str) -> Optional[ApprovalRecord]:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT * FROM approvals
                    WHERE task_id=? AND generation=?
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (task_id, generation),
                ).fetchone()
            finally:
                self._close(connection)
        return self._approval_from_row(row) if row else None

    def approval_by_token_hash(self, token_hash: str) -> Optional[ApprovalRecord]:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    "SELECT * FROM approvals WHERE token_hash=?", (token_hash,)
                ).fetchone()
            finally:
                self._close(connection)
        return self._approval_from_row(row) if row else None

    def add_evidence(self, record: EvidenceRecord) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO evidence(
                        id, task_id, generation, kind, artifact_digest, record_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.task_id,
                        record.generation,
                        record.kind.value,
                        record.artifact_digest,
                        record.model_dump_json(),
                        record.created_at.isoformat(),
                    ),
                )
                self._commit(connection)
            finally:
                self._close(connection)

    def list_evidence(self, task_id: str, generation: str) -> List[EvidenceRecord]:
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT record_json FROM evidence
                    WHERE task_id=? AND generation=? ORDER BY created_at, id
                    """,
                    (task_id, generation),
                ).fetchall()
            finally:
                self._close(connection)
        return [EvidenceRecord.model_validate_json(row["record_json"]) for row in rows]

    def add_memory_candidate(self, record: MemoryCandidate) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO memory_candidates(
                        id, task_id, generation, evidence_digest, review_id,
                        record_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.task_id,
                        record.generation,
                        record.evidence_digest,
                        record.review_id,
                        record.model_dump_json(),
                        record.created_at.isoformat(),
                    ),
                )
                self._commit(connection)
            finally:
                self._close(connection)

    def list_memory_candidates(
        self, task_id: str, generation: str
    ) -> List[MemoryCandidate]:
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT record_json FROM memory_candidates
                    WHERE task_id=? AND generation=? ORDER BY created_at, id
                    """,
                    (task_id, generation),
                ).fetchall()
            finally:
                self._close(connection)
        return [MemoryCandidate.model_validate_json(row["record_json"]) for row in rows]

    def add_memory(self, record: MemoryRecord) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO memories(
                        id, task_id, generation, validated, record_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.task_id,
                        record.generation,
                        1 if record.validated else 0,
                        record.model_dump_json(),
                        record.created_at.isoformat(),
                    ),
                )
                self._commit(connection)
            finally:
                self._close(connection)

    def list_memories(self, task_id: str, generation: str) -> List[MemoryRecord]:
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT record_json FROM memories
                    WHERE task_id=? AND generation=? ORDER BY created_at, id
                    """,
                    (task_id, generation),
                ).fetchall()
            finally:
                self._close(connection)
        return [MemoryRecord.model_validate_json(row["record_json"]) for row in rows]

    def append_event(
        self,
        task_id: str,
        generation: str,
        event_type: str,
        actor: str,
        stage: Optional[Stage],
        payload: Dict[str, Any],
        created_at: Optional[datetime] = None,
    ) -> AuditEvent:
        timestamp = created_at or utc_now()
        event_id = "evt_%s" % uuid.uuid4().hex
        with self._lock:
            connection = self._connect()
            try:
                # Choose the predecessor and insert its successor in one write transaction.
                if self._active_transaction() is None:
                    connection.execute("BEGIN IMMEDIATE")
                previous_row = connection.execute(
                    """
                    SELECT event_hash FROM audit_events
                    WHERE task_id=? AND generation=?
                    ORDER BY sequence DESC LIMIT 1
                    """,
                    (task_id, generation),
                ).fetchone()
                previous_hash = previous_row["event_hash"] if previous_row else "0" * 64
                hash_payload = {
                    "id": event_id,
                    "task_id": task_id,
                    "generation": generation,
                    "event_type": event_type,
                    "actor": actor,
                    "stage": stage.value if stage else None,
                    "payload": payload,
                    "previous_hash": previous_hash,
                    "created_at": timestamp.isoformat(),
                }
                event_hash = canonical_sha256(hash_payload)
                cursor = connection.execute(
                    """
                    INSERT INTO audit_events(
                        id, task_id, generation, event_type, actor, stage, payload_json,
                        previous_hash, event_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        task_id,
                        generation,
                        event_type,
                        actor,
                        stage.value if stage else None,
                        json.dumps(payload, sort_keys=True, separators=(",", ":")),
                        previous_hash,
                        event_hash,
                        timestamp.isoformat(),
                    ),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("SQLite did not return an audit-event sequence")
                sequence = int(cursor.lastrowid)
                self._commit(connection)
            except Exception:
                self._rollback(connection)
                raise
            finally:
                self._close(connection)
        return AuditEvent(
            sequence=sequence,
            id=event_id,
            task_id=task_id,
            generation=generation,
            event_type=event_type,
            actor=actor,
            stage=stage,
            payload=payload,
            previous_hash=previous_hash,
            event_hash=event_hash,
            created_at=timestamp,
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> AuditEvent:
        return AuditEvent(
            sequence=row["sequence"],
            id=row["id"],
            task_id=row["task_id"],
            generation=row["generation"],
            event_type=row["event_type"],
            actor=row["actor"],
            stage=Stage(row["stage"]) if row["stage"] else None,
            payload=json.loads(row["payload_json"]),
            previous_hash=row["previous_hash"],
            event_hash=row["event_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def list_events(
        self, task_id: str, generation: str, after_sequence: int = 0, limit: int = 200
    ) -> List[AuditEvent]:
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT * FROM audit_events
                    WHERE task_id=? AND generation=? AND sequence>?
                    ORDER BY sequence LIMIT ?
                    """,
                    (task_id, generation, after_sequence, limit),
                ).fetchall()
            finally:
                self._close(connection)
        return [self._event_from_row(row) for row in rows]

    def verify_event_chain(self, task_id: str, generation: str) -> bool:
        events = self.list_events(task_id, generation, after_sequence=0, limit=1000)
        previous_hash = "0" * 64
        for event in events:
            expected_hash = canonical_sha256(
                {
                    "id": event.id,
                    "task_id": event.task_id,
                    "generation": event.generation,
                    "event_type": event.event_type,
                    "actor": event.actor,
                    "stage": event.stage.value if event.stage else None,
                    "payload": event.payload,
                    "previous_hash": previous_hash,
                    "created_at": event.created_at.isoformat(),
                }
            )
            if event.previous_hash != previous_hash or event.event_hash != expected_hash:
                return False
            previous_hash = event.event_hash
        return True

    def recent_events(
        self,
        limit: int = 30,
        task_id: Optional[str] = None,
        generation: Optional[str] = None,
    ) -> List[AuditEvent]:
        with self._lock:
            connection = self._connect()
            try:
                if task_id is not None and generation is not None:
                    rows = connection.execute(
                        """
                        SELECT * FROM audit_events
                        WHERE task_id=? AND generation=?
                        ORDER BY sequence DESC LIMIT ?
                        """,
                        (task_id, generation, limit),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        "SELECT * FROM audit_events ORDER BY sequence DESC LIMIT ?", (limit,)
                    ).fetchall()
            finally:
                self._close(connection)
        return [self._event_from_row(row) for row in reversed(rows)]

    def get_idempotent(
        self, method: str, path: str, key: str, request_hash: str
    ) -> Optional[Tuple[int, Dict[str, Any]]]:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT request_hash, response_json, status_code FROM idempotency
                    WHERE method=? AND path=? AND key=?
                    """,
                    (method, path, key),
                ).fetchone()
            finally:
                self._close(connection)
        if row is None:
            return None
        if row["request_hash"] != request_hash:
            raise ConflictError(
                "idempotency_key_conflict",
                "Idempotency key was already used with a different request body",
                {"key": key, "method": method, "path": path},
            )
        return int(row["status_code"]), json.loads(row["response_json"])

    def put_idempotent(
        self,
        method: str,
        path: str,
        key: str,
        request_hash: str,
        status_code: int,
        response: Dict[str, Any],
    ) -> None:
        with self._lock:
            connection = self._connect()
            try:
                connection.execute(
                    """
                    INSERT INTO idempotency(
                        method, path, key, request_hash, response_json, status_code, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        method,
                        path,
                        key,
                        request_hash,
                        json.dumps(response, sort_keys=True, separators=(",", ":")),
                        status_code,
                        utc_now().isoformat(),
                    ),
                )
                self._commit(connection)
            except sqlite3.IntegrityError:
                self._rollback(connection)
                existing = self.get_idempotent(method, path, key, request_hash)
                if existing is None:
                    raise
            finally:
                self._close(connection)

    def counts(self) -> Dict[str, int]:
        with self._lock:
            connection = self._connect()
            try:
                return {
                    "tasks": connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
                    "approvals": connection.execute("SELECT COUNT(*) FROM approvals").fetchone()[0],
                    "evidence": connection.execute("SELECT COUNT(*) FROM evidence").fetchone()[0],
                    "memory_candidates": connection.execute(
                        "SELECT COUNT(*) FROM memory_candidates"
                    ).fetchone()[0],
                    "events": connection.execute("SELECT COUNT(*) FROM audit_events").fetchone()[0],
                    "validated_memories": connection.execute(
                        "SELECT COUNT(*) FROM memories WHERE validated=1"
                    ).fetchone()[0],
                }
            finally:
                self._close(connection)
