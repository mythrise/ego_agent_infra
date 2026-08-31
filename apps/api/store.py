"""SQLite persistence for tasks, approvals, evidence, memory, and hash-chained audit events."""

import fcntl
import hashlib
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
from .store_contract import (
    DecisionClosureRecord,
    TrustedMemoryCurrent,
    TrustedMemoryEvent,
    TrustedMemoryRecord,
    trusted_memory_event_hash,
    trusted_memory_record_type,
)
from .trusted_memory.models import (
    CandidateFact,
    ConflictRecord,
    LifecycleTransition,
    LegacyMemoryView,
    MemoryState,
    RevocationRecord,
    SupersessionRecord,
    TrustedFact,
)
from benchmarks.secure_memory.canonical import (
    canonical_bytes,
    canonical_sha256 as secure_memory_canonical_sha256,
    parse_json_bytes,
    validate_sha256_digest,
)


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

CREATE TABLE IF NOT EXISTS trusted_memory_streams (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence >= 0),
    stream_root TEXT NOT NULL CHECK(length(stream_root) = 64),
    PRIMARY KEY(tenant_id, project_id, lineage_id)
);

CREATE TABLE IF NOT EXISTS trusted_memory_history (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    event_type TEXT NOT NULL,
    record_bytes BLOB NOT NULL,
    record_sha256 TEXT NOT NULL CHECK(length(record_sha256) = 64),
    previous_hash TEXT NOT NULL CHECK(length(previous_hash) = 64),
    event_hash TEXT NOT NULL UNIQUE CHECK(length(event_hash) = 64),
    idempotency_key TEXT NOT NULL,
    PRIMARY KEY(tenant_id, project_id, lineage_id, sequence),
    UNIQUE(tenant_id, project_id, lineage_id, idempotency_key)
);

CREATE TRIGGER IF NOT EXISTS trusted_memory_history_no_update
BEFORE UPDATE ON trusted_memory_history
BEGIN
    SELECT RAISE(ABORT, 'trusted memory history is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trusted_memory_history_no_delete
BEFORE DELETE ON trusted_memory_history
BEGIN
    SELECT RAISE(ABORT, 'trusted memory history is immutable');
END;

CREATE TABLE IF NOT EXISTS trusted_memory_current (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    revision_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK(revision > 0),
    fact_digest TEXT NOT NULL CHECK(length(fact_digest) = 64),
    state TEXT NOT NULL,
    eligible INTEGER NOT NULL CHECK(eligible IN (0, 1)),
    fact_bytes BLOB NOT NULL,
    fact_event_hash TEXT NOT NULL CHECK(length(fact_event_hash) = 64),
    projection_event_hash TEXT NOT NULL CHECK(length(projection_event_hash) = 64),
    PRIMARY KEY(tenant_id, project_id, lineage_id)
);

CREATE TRIGGER IF NOT EXISTS trusted_memory_current_no_delete
BEFORE DELETE ON trusted_memory_current
BEGIN
    SELECT RAISE(ABORT, 'trusted memory current projection cannot be deleted directly');
END;

CREATE TRIGGER IF NOT EXISTS trusted_memory_current_guard_insert
BEFORE INSERT ON trusted_memory_current
WHEN NOT EXISTS (
    SELECT 1
    FROM trusted_memory_history AS history
    JOIN trusted_memory_streams AS stream
      ON stream.tenant_id = history.tenant_id
     AND stream.project_id = history.project_id
     AND stream.lineage_id = history.lineage_id
     AND stream.sequence = history.sequence
     AND stream.stream_root = history.event_hash
    WHERE history.tenant_id = NEW.tenant_id
      AND history.project_id = NEW.project_id
      AND history.lineage_id = NEW.lineage_id
      AND history.event_hash = NEW.projection_event_hash
)
BEGIN
    SELECT RAISE(ABORT, 'trusted memory current projection requires latest history event');
END;

CREATE TRIGGER IF NOT EXISTS trusted_memory_current_guard_update
BEFORE UPDATE ON trusted_memory_current
WHEN NEW.projection_event_hash = OLD.projection_event_hash OR NOT EXISTS (
    SELECT 1
    FROM trusted_memory_history AS history
    JOIN trusted_memory_streams AS stream
      ON stream.tenant_id = history.tenant_id
     AND stream.project_id = history.project_id
     AND stream.lineage_id = history.lineage_id
     AND stream.sequence = history.sequence
     AND stream.stream_root = history.event_hash
    WHERE history.tenant_id = NEW.tenant_id
      AND history.project_id = NEW.project_id
      AND history.lineage_id = NEW.lineage_id
      AND history.event_hash = NEW.projection_event_hash
)
BEGIN
    SELECT RAISE(ABORT, 'trusted memory current projection requires new latest history event');
END;

CREATE TABLE IF NOT EXISTS trusted_memory_closures (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    event_hash TEXT NOT NULL,
    closure_digest TEXT NOT NULL CHECK(length(closure_digest) = 64),
    PRIMARY KEY(tenant_id, project_id, lineage_id, event_hash, closure_digest)
);

CREATE TRIGGER IF NOT EXISTS trusted_memory_closures_no_update
BEFORE UPDATE ON trusted_memory_closures
BEGIN
    SELECT RAISE(ABORT, 'trusted memory closures are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trusted_memory_closures_no_delete
BEFORE DELETE ON trusted_memory_closures
BEGIN
    SELECT RAISE(ABORT, 'trusted memory closures are immutable');
END;

CREATE TABLE IF NOT EXISTS trusted_memory_decision_closures (
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    closure_digest TEXT NOT NULL CHECK(length(closure_digest) = 64),
    closure_bytes BLOB NOT NULL,
    closure_bytes_sha256 TEXT NOT NULL CHECK(length(closure_bytes_sha256) = 64),
    idempotency_key TEXT NOT NULL,
    PRIMARY KEY(tenant_id, project_id, closure_digest),
    UNIQUE(tenant_id, project_id, idempotency_key)
);

CREATE TRIGGER IF NOT EXISTS trusted_memory_decision_closures_no_update
BEFORE UPDATE ON trusted_memory_decision_closures
BEGIN
    SELECT RAISE(ABORT, 'trusted memory decision closures are immutable');
END;

CREATE TRIGGER IF NOT EXISTS trusted_memory_decision_closures_no_delete
BEFORE DELETE ON trusted_memory_decision_closures
BEGIN
    SELECT RAISE(ABORT, 'trusted memory decision closures are immutable');
END;

CREATE TABLE IF NOT EXISTS trusted_memory_outbox (
    event_hash TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    lineage_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence > 0),
    payload_json TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS trusted_memory_outbox_no_update
BEFORE UPDATE ON trusted_memory_outbox
BEGIN
    SELECT RAISE(ABORT, 'trusted memory outbox is immutable');
END;

CREATE TRIGGER IF NOT EXISTS trusted_memory_outbox_no_delete
BEFORE DELETE ON trusted_memory_outbox
BEGIN
    SELECT RAISE(ABORT, 'trusted memory outbox is immutable');
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
                        raise RuntimeError(
                            "nested database mutation failed; transaction rolled back"
                        )
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

    def list_memory_candidates(self, task_id: str, generation: str) -> List[MemoryCandidate]:
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

    @staticmethod
    def _trusted_memory_event_from_row(row: sqlite3.Row) -> TrustedMemoryEvent:
        return TrustedMemoryEvent(
            tenant_id=str(row["tenant_id"]),
            project_id=str(row["project_id"]),
            lineage_id=str(row["lineage_id"]),
            sequence=int(row["sequence"]),
            event_type=str(row["event_type"]),
            record_bytes=bytes(row["record_bytes"]),
            record_sha256=str(row["record_sha256"]),
            previous_hash=str(row["previous_hash"]),
            event_hash=str(row["event_hash"]),
        )

    @staticmethod
    def _decision_closure_from_row(row: sqlite3.Row) -> DecisionClosureRecord:
        return DecisionClosureRecord(
            tenant_id=str(row["tenant_id"]),
            project_id=str(row["project_id"]),
            closure_digest=str(row["closure_digest"]),
            closure_bytes=bytes(row["closure_bytes"]),
            closure_bytes_sha256=str(row["closure_bytes_sha256"]),
            idempotency_key=str(row["idempotency_key"]),
        )

    def append_decision_closure(
        self,
        *,
        tenant_id: str,
        project_id: str,
        closure_digest: str,
        closure_bytes: bytes,
        idempotency_key: str,
    ) -> DecisionClosureRecord:
        validate_sha256_digest(closure_digest)
        if not tenant_id or not project_id or not idempotency_key:
            raise ValueError("decision closure scope and idempotency key must be non-empty")
        if not isinstance(closure_bytes, bytes) or not closure_bytes:
            raise TypeError("decision closure bytes must be non-empty bytes")
        try:
            parsed = parse_json_bytes(closure_bytes)
        except (TypeError, ValueError) as exc:
            raise ValueError("decision closure bytes must be canonical JSON") from exc
        if canonical_bytes(parsed) != closure_bytes or not isinstance(parsed, dict):
            raise ValueError("decision closure bytes must be one canonical JSON object")
        if (
            parsed.get("closure_digest") != closure_digest
            or not isinstance(parsed.get("core"), dict)
            or secure_memory_canonical_sha256("trusted-memory-decision-closure", parsed["core"])
            != closure_digest
        ):
            raise ValueError("decision closure digest does not match canonical bytes")
        bytes_sha256 = hashlib.sha256(closure_bytes).hexdigest()
        with self.transaction():
            with self._lock:
                connection = self._connect()
                existing_key = connection.execute(
                    """
                    SELECT * FROM trusted_memory_decision_closures
                    WHERE tenant_id=? AND project_id=? AND idempotency_key=?
                    """,
                    (tenant_id, project_id, idempotency_key),
                ).fetchone()
                if existing_key is not None:
                    if (
                        str(existing_key["closure_digest"]) != closure_digest
                        or bytes(existing_key["closure_bytes"]) != closure_bytes
                    ):
                        raise ConflictError(
                            "decision_closure_idempotency_conflict",
                            "Decision closure idempotency key has different canonical bytes",
                            {"idempotency_key": idempotency_key},
                        )
                    return self._decision_closure_from_row(existing_key)
                existing_digest = connection.execute(
                    """
                    SELECT * FROM trusted_memory_decision_closures
                    WHERE tenant_id=? AND project_id=? AND closure_digest=?
                    """,
                    (tenant_id, project_id, closure_digest),
                ).fetchone()
                if existing_digest is not None:
                    if bytes(existing_digest["closure_bytes"]) != closure_bytes:
                        raise ConflictError(
                            "decision_closure_bytes_conflict",
                            "Decision closure digest has different canonical bytes",
                            {"closure_digest": closure_digest},
                        )
                    return self._decision_closure_from_row(existing_digest)
                connection.execute(
                    """
                    INSERT INTO trusted_memory_decision_closures(
                        tenant_id, project_id, closure_digest, closure_bytes,
                        closure_bytes_sha256, idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        project_id,
                        closure_digest,
                        closure_bytes,
                        bytes_sha256,
                        idempotency_key,
                    ),
                )
        return DecisionClosureRecord(
            tenant_id=tenant_id,
            project_id=project_id,
            closure_digest=closure_digest,
            closure_bytes=closure_bytes,
            closure_bytes_sha256=bytes_sha256,
            idempotency_key=idempotency_key,
        )

    def get_decision_closure(
        self, *, tenant_id: str, project_id: str, closure_digest: str
    ) -> DecisionClosureRecord:
        validate_sha256_digest(closure_digest)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM trusted_memory_decision_closures
                WHERE tenant_id=? AND project_id=? AND closure_digest=?
                """,
                (tenant_id, project_id, closure_digest),
            ).fetchone()
        finally:
            if (
                connection is not self._memory_connection
                and connection is not self._active_transaction()
            ):
                connection.close()
        if row is None:
            raise NotFoundError("decision_closure", closure_digest)
        record = self._decision_closure_from_row(row)
        if hashlib.sha256(record.closure_bytes).hexdigest() != record.closure_bytes_sha256:
            raise ValueError("stored decision closure bytes digest mismatch")
        return record

    def append_trusted_memory_record(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
        record: TrustedMemoryRecord,
        idempotency_key: str,
        expected_current_event_hash: Optional[str] = None,
    ) -> TrustedMemoryEvent:
        closure_digests: Tuple[str, ...]
        if isinstance(record, (CandidateFact, TrustedFact)):
            record_scope = record.scope
            record_lineage_matches = record.lineage_id == lineage_id
            closure_digests = (
                (record.provenance.decision_closure_digest,)
                if isinstance(record, TrustedFact)
                else ()
            )
        elif isinstance(record, LifecycleTransition):
            record_scope = record.core.scope
            record_lineage_matches = record.core.lineage_id == lineage_id
            closure_digests = (
                (record.core.decision_closure_digest,)
                if record.core.decision_closure_digest is not None
                else ()
            )
        elif isinstance(record, ConflictRecord):
            record_scope = record.group.scope
            record_lineage_matches = any(
                member.lineage_id == lineage_id for member in record.group.members
            )
            closure_digests = record.group.decision_closure_digests
        elif isinstance(record, SupersessionRecord):
            record_scope = record.core.scope
            record_lineage_matches = record.core.lineage_id == lineage_id
            closure_digests = (record.core.decision_closure_digest,)
        elif isinstance(record, RevocationRecord):
            record_scope = record.core.scope
            record_lineage_matches = record.core.lineage_id == lineage_id
            closure_digests = (record.core.decision_closure_digest,)
        else:
            raise TypeError("unsupported trusted-memory record")
        if (
            record_scope.tenant_id != tenant_id
            or record_scope.project_id != project_id
            or not record_lineage_matches
        ):
            raise ValueError("trusted-memory append scope does not match record scope")
        if not idempotency_key:
            raise ValueError("trusted-memory idempotency_key must not be empty")
        if isinstance(record, CandidateFact) and expected_current_event_hash is not None:
            raise ConflictError(
                "trusted_memory_candidate_promotion",
                "A candidate cannot update the trusted current projection",
                {"lineage_id": lineage_id},
            )

        record_bytes = canonical_bytes(record)
        record_sha256 = hashlib.sha256(record_bytes).hexdigest()
        event_type = trusted_memory_record_type(record)
        with self.transaction():
            with self._lock:
                connection = self._connect()
                existing = connection.execute(
                    """
                    SELECT * FROM trusted_memory_history
                    WHERE tenant_id=? AND project_id=? AND lineage_id=?
                      AND idempotency_key=?
                    """,
                    (tenant_id, project_id, lineage_id, idempotency_key),
                ).fetchone()
                if existing is not None:
                    if bytes(existing["record_bytes"]) != record_bytes:
                        raise ConflictError(
                            "trusted_memory_idempotency_conflict",
                            "Trusted-memory idempotency key has different canonical bytes",
                            {"idempotency_key": idempotency_key},
                        )
                    return self._trusted_memory_event_from_row(existing)

                current = connection.execute(
                    """
                    SELECT * FROM trusted_memory_current
                    WHERE tenant_id=? AND project_id=? AND lineage_id=?
                    """,
                    (tenant_id, project_id, lineage_id),
                ).fetchone()
                if isinstance(record, TrustedFact):
                    if current is None:
                        if expected_current_event_hash is not None or record.revision != 1:
                            raise ConflictError(
                                "trusted_memory_projection_conflict",
                                "Trusted-memory compare-and-swap does not match current projection",
                                {"lineage_id": lineage_id},
                            )
                    elif (
                        expected_current_event_hash != current["projection_event_hash"]
                        or record.revision != int(current["revision"]) + 1
                    ):
                        raise ConflictError(
                            "trusted_memory_projection_conflict",
                            "Trusted-memory compare-and-swap does not match current projection",
                            {"lineage_id": lineage_id},
                        )
                elif not isinstance(record, CandidateFact):
                    if (
                        current is None
                        or expected_current_event_hash != current["projection_event_hash"]
                    ):
                        raise ConflictError(
                            "trusted_memory_projection_conflict",
                            "Trusted-memory compare-and-swap does not match current projection",
                            {"lineage_id": lineage_id},
                        )
                    if isinstance(record, LifecycleTransition):
                        projection_matches = (
                            record.core.fact_digest == current["fact_digest"]
                            and record.core.from_revision == int(current["revision"])
                            and record.core.from_state.value == current["state"]
                        )
                    elif isinstance(record, ConflictRecord):
                        projection_matches = any(
                            member.lineage_id == lineage_id
                            and member.revision_id == current["revision_id"]
                            and member.revision == int(current["revision"])
                            and member.fact_digest == current["fact_digest"]
                            for member in record.group.members
                        )
                    elif isinstance(record, SupersessionRecord):
                        projection_matches = record.core.superseded_revision_id == current[
                            "revision_id"
                        ] and record.core.superseded_revision == int(current["revision"])
                    else:
                        projection_matches = (
                            record.core.revision_id == current["revision_id"]
                            and record.core.revision == int(current["revision"])
                            and record.core.expected_revision == int(current["revision"])
                            and record.core.fact_digest == current["fact_digest"]
                        )
                    if not projection_matches:
                        raise ConflictError(
                            "trusted_memory_projection_conflict",
                            "Trusted-memory event does not match the current fact projection",
                            {"lineage_id": lineage_id},
                        )

                stream = connection.execute(
                    """
                    SELECT sequence, stream_root FROM trusted_memory_streams
                    WHERE tenant_id=? AND project_id=? AND lineage_id=?
                    """,
                    (tenant_id, project_id, lineage_id),
                ).fetchone()
                sequence = int(stream["sequence"]) + 1 if stream else 1
                previous_hash = str(stream["stream_root"]) if stream else "0" * 64
                event_hash = trusted_memory_event_hash(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    lineage_id=lineage_id,
                    sequence=sequence,
                    event_type=event_type,
                    record_sha256=record_sha256,
                    previous_hash=previous_hash,
                )
                connection.execute(
                    """
                    INSERT INTO trusted_memory_history(
                        tenant_id, project_id, lineage_id, sequence, event_type,
                        record_bytes, record_sha256, previous_hash, event_hash,
                        idempotency_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tenant_id,
                        project_id,
                        lineage_id,
                        sequence,
                        event_type,
                        record_bytes,
                        record_sha256,
                        previous_hash,
                        event_hash,
                        idempotency_key,
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO trusted_memory_streams(
                        tenant_id, project_id, lineage_id, sequence, stream_root
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(tenant_id, project_id, lineage_id) DO UPDATE SET
                        sequence=excluded.sequence,
                        stream_root=excluded.stream_root
                    """,
                    (tenant_id, project_id, lineage_id, sequence, event_hash),
                )
                for closure_digest in closure_digests:
                    connection.execute(
                        """
                        INSERT INTO trusted_memory_closures(
                            tenant_id, project_id, lineage_id, event_hash, closure_digest
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            tenant_id,
                            project_id,
                            lineage_id,
                            event_hash,
                            closure_digest,
                        ),
                    )
                if isinstance(record, TrustedFact):
                    if current is None:
                        connection.execute(
                            """
                            INSERT INTO trusted_memory_current(
                                tenant_id, project_id, lineage_id, revision_id, revision,
                                fact_digest, state, eligible, fact_bytes, fact_event_hash,
                                projection_event_hash
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                            """,
                            (
                                tenant_id,
                                project_id,
                                lineage_id,
                                record.revision_id,
                                record.revision,
                                record.trusted_fact_digest,
                                record.state.value,
                                record_bytes,
                                event_hash,
                                event_hash,
                            ),
                        )
                    else:
                        cursor = connection.execute(
                            """
                            UPDATE trusted_memory_current SET
                                revision_id=?, revision=?, fact_digest=?, state=?, eligible=1,
                                fact_bytes=?, fact_event_hash=?, projection_event_hash=?
                            WHERE tenant_id=? AND project_id=? AND lineage_id=?
                              AND projection_event_hash=?
                            """,
                            (
                                record.revision_id,
                                record.revision,
                                record.trusted_fact_digest,
                                record.state.value,
                                record_bytes,
                                event_hash,
                                event_hash,
                                tenant_id,
                                project_id,
                                lineage_id,
                                expected_current_event_hash,
                            ),
                        )
                        if cursor.rowcount != 1:
                            raise ConflictError(
                                "trusted_memory_projection_conflict",
                                "Trusted-memory compare-and-swap lost a concurrent update",
                                {"lineage_id": lineage_id},
                            )
                elif not isinstance(record, CandidateFact):
                    if isinstance(record, LifecycleTransition):
                        projected_state = record.core.to_state
                    elif isinstance(record, ConflictRecord):
                        projected_state = MemoryState.CONFLICTED
                    elif isinstance(record, SupersessionRecord):
                        projected_state = MemoryState.SUPERSEDED
                    else:
                        projected_state = MemoryState.REVOKED
                    cursor = connection.execute(
                        """
                        UPDATE trusted_memory_current SET
                            state=?, eligible=?, projection_event_hash=?
                        WHERE tenant_id=? AND project_id=? AND lineage_id=?
                          AND projection_event_hash=?
                        """,
                        (
                            projected_state.value,
                            1 if projected_state is MemoryState.VALIDATED else 0,
                            event_hash,
                            tenant_id,
                            project_id,
                            lineage_id,
                            expected_current_event_hash,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise ConflictError(
                            "trusted_memory_projection_conflict",
                            "Trusted-memory compare-and-swap lost a concurrent update",
                            {"lineage_id": lineage_id},
                        )
                outbox_payload = {
                    "event_hash": event_hash,
                    "event_type": event_type,
                    "lineage_id": lineage_id,
                    "project_id": project_id,
                    "record_sha256": record_sha256,
                    "sequence": sequence,
                    "stream_root": event_hash,
                    "tenant_id": tenant_id,
                }
                connection.execute(
                    """
                    INSERT INTO trusted_memory_outbox(
                        event_hash, tenant_id, project_id, lineage_id, sequence, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_hash,
                        tenant_id,
                        project_id,
                        lineage_id,
                        sequence,
                        json.dumps(outbox_payload, sort_keys=True, separators=(",", ":")),
                    ),
                )
        return TrustedMemoryEvent(
            tenant_id=tenant_id,
            project_id=project_id,
            lineage_id=lineage_id,
            sequence=sequence,
            event_type=event_type,
            record_bytes=record_bytes,
            record_sha256=record_sha256,
            previous_hash=previous_hash,
            event_hash=event_hash,
        )

    def get_trusted_memory_event(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
        event_hash: str,
    ) -> TrustedMemoryEvent:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT * FROM trusted_memory_history
                    WHERE tenant_id=? AND project_id=? AND lineage_id=? AND event_hash=?
                    """,
                    (tenant_id, project_id, lineage_id, event_hash),
                ).fetchone()
            finally:
                self._close(connection)
        if row is None:
            raise NotFoundError("trusted_memory_event", event_hash)
        return self._trusted_memory_event_from_row(row)

    def list_trusted_memory_history(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
        after_sequence: int = 0,
        limit: int = 1000,
    ) -> List[TrustedMemoryEvent]:
        with self._lock:
            connection = self._connect()
            try:
                rows = connection.execute(
                    """
                    SELECT * FROM trusted_memory_history
                    WHERE tenant_id=? AND project_id=? AND lineage_id=? AND sequence>?
                    ORDER BY sequence LIMIT ?
                    """,
                    (tenant_id, project_id, lineage_id, after_sequence, limit),
                ).fetchall()
            finally:
                self._close(connection)
        return [self._trusted_memory_event_from_row(row) for row in rows]

    def get_current_trusted_fact(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
    ) -> Optional[TrustedMemoryCurrent]:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT * FROM trusted_memory_current
                    WHERE tenant_id=? AND project_id=? AND lineage_id=? AND eligible=1
                    """,
                    (tenant_id, project_id, lineage_id),
                ).fetchone()
            finally:
                self._close(connection)
        if row is None:
            return None
        return TrustedMemoryCurrent(
            tenant_id=str(row["tenant_id"]),
            project_id=str(row["project_id"]),
            lineage_id=str(row["lineage_id"]),
            revision_id=str(row["revision_id"]),
            revision=int(row["revision"]),
            fact_digest=str(row["fact_digest"]),
            state=MemoryState(str(row["state"])),
            fact_bytes=bytes(row["fact_bytes"]),
            fact_event_hash=str(row["fact_event_hash"]),
            projection_event_hash=str(row["projection_event_hash"]),
        )

    def get_trusted_memory_stream_root(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
    ) -> str:
        with self._lock:
            connection = self._connect()
            try:
                row = connection.execute(
                    """
                    SELECT stream_root FROM trusted_memory_streams
                    WHERE tenant_id=? AND project_id=? AND lineage_id=?
                    """,
                    (tenant_id, project_id, lineage_id),
                ).fetchone()
            finally:
                self._close(connection)
        return str(row["stream_root"]) if row else "0" * 64

    def verify_trusted_memory_stream(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
    ) -> bool:
        previous_hash = "0" * 64
        last_sequence = 0
        while True:
            events = self.list_trusted_memory_history(
                tenant_id=tenant_id,
                project_id=project_id,
                lineage_id=lineage_id,
                after_sequence=last_sequence,
                limit=1000,
            )
            if not events:
                break
            for event in events:
                if event.sequence != last_sequence + 1:
                    return False
                if hashlib.sha256(event.record_bytes).hexdigest() != event.record_sha256:
                    return False
                expected_hash = trusted_memory_event_hash(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    lineage_id=lineage_id,
                    sequence=event.sequence,
                    event_type=event.event_type,
                    record_sha256=event.record_sha256,
                    previous_hash=previous_hash,
                )
                if event.previous_hash != previous_hash or event.event_hash != expected_hash:
                    return False
                previous_hash = event.event_hash
                last_sequence = event.sequence

        with self._lock:
            connection = self._connect()
            try:
                stream = connection.execute(
                    """
                    SELECT sequence, stream_root FROM trusted_memory_streams
                    WHERE tenant_id=? AND project_id=? AND lineage_id=?
                    """,
                    (tenant_id, project_id, lineage_id),
                ).fetchone()
            finally:
                self._close(connection)
        if stream is None:
            return last_sequence == 0 and previous_hash == "0" * 64
        return int(stream["sequence"]) == last_sequence and stream["stream_root"] == previous_hash

    def list_legacy_memory_views(
        self,
        *,
        task_id: str,
        generation: str,
        tenant_id: str,
        project_id: str,
        version: str,
    ) -> List[LegacyMemoryView]:
        return [
            LegacyMemoryView.from_memory_record(
                record,
                tenant_id=tenant_id,
                project_id=project_id,
                version=version,
            )
            for record in self.list_memories(task_id, generation)
        ]

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
