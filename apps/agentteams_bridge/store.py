"""Durable bridge state and tamper-evident event ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from .errors import BridgeError
from .models import BridgeRun, CollaborationEnvelope, RunState, canonical_json, utc_now


ZERO_HASH = "0" * 64
OPERATION_LEASE_KEY = "_operation_lease"


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise BridgeError(
            "event_time_invalid",
            "Bridge ledger timestamps must carry an explicit timezone",
        )
    return value.astimezone(timezone.utc).isoformat()


def _operation_lease(checkpoint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = checkpoint.get(OPERATION_LEASE_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise BridgeError(
            "operation_lease_invalid",
            "Persisted bridge operation lease is malformed; refusing to steal it",
            retryable=False,
        )
    required = {"operation", "owner_id", "acquired_at", "expires_at", "timeout_seconds"}
    if not required.issubset(raw):
        raise BridgeError(
            "operation_lease_invalid",
            "Persisted bridge operation lease is incomplete; refusing to steal it",
            retryable=False,
        )
    return raw


def _lease_expiry(lease: Dict[str, Any]) -> datetime:
    try:
        expires_at = datetime.fromisoformat(str(lease["expires_at"]))
    except (TypeError, ValueError) as error:
        raise BridgeError(
            "operation_lease_invalid",
            "Persisted bridge operation lease has an invalid expiry; refusing to steal it",
            retryable=False,
        ) from error
    if expires_at.tzinfo is None or expires_at.utcoffset() is None:
        raise BridgeError(
            "operation_lease_invalid",
            "Persisted bridge operation lease expiry must be timezone-aware",
            retryable=False,
        )
    return expires_at.astimezone(timezone.utc)


def _lease_payload(
    operation: str, owner_id: str, acquired_at: datetime, timeout_seconds: int
) -> Dict[str, Any]:
    if not operation or not owner_id or not 1 <= timeout_seconds <= 3600:
        raise ValueError("operation, owner_id, and a 1..3600 second lease timeout are required")
    if acquired_at.tzinfo is None or acquired_at.utcoffset() is None:
        raise ValueError("operation lease acquisition time must be timezone-aware")
    acquired = acquired_at.astimezone(timezone.utc)
    return {
        "operation": operation,
        "owner_id": owner_id,
        "acquired_at": _utc_iso(acquired),
        "expires_at": _utc_iso(acquired + timedelta(seconds=timeout_seconds)),
        "timeout_seconds": timeout_seconds,
    }


def _raise_if_lease_held(checkpoint: Dict[str, Any], *, run_id: str, acquired_at: datetime) -> None:
    lease = _operation_lease(checkpoint)
    if lease is None or _lease_expiry(lease) <= acquired_at.astimezone(timezone.utc):
        return
    raise BridgeError(
        "operation_in_progress",
        "Another bridge process owns the run operation lease",
        status_code=409,
        retryable=True,
        details={
            "run_id": run_id,
            "operation": lease["operation"],
            "expires_at": lease["expires_at"],
            "timeout_seconds": lease["timeout_seconds"],
        },
    )


def _assert_update_lease(
    checkpoint: Dict[str, Any],
    next_checkpoint: Dict[str, Any],
    *,
    run_id: str,
    lease_owner: Optional[str],
    checked_at: datetime,
) -> None:
    lease = _operation_lease(checkpoint)
    next_lease = _operation_lease(next_checkpoint)
    current_owner = str(lease["owner_id"]) if lease is not None else None
    if current_owner is None and lease_owner is None:
        if next_lease is not None:
            raise BridgeError(
                "operation_lease_mutation",
                "Run updates may not create an operation lease",
                status_code=409,
                retryable=False,
                details={"run_id": run_id},
            )
        return
    if current_owner == lease_owner and lease is not None:
        expires_at = _lease_expiry(lease)
        if expires_at <= checked_at.astimezone(timezone.utc):
            raise BridgeError(
                "operation_lease_lost",
                "Bridge operation lease expired before the state transition",
                status_code=409,
                retryable=True,
                details={
                    "run_id": run_id,
                    "operation": lease["operation"],
                    "expires_at": lease["expires_at"],
                    "reason": "expired",
                },
            )
        if next_lease != lease:
            raise BridgeError(
                "operation_lease_mutation",
                "Run updates may not remove, replace, or extend the operation lease",
                status_code=409,
                retryable=False,
                details={"run_id": run_id},
            )
        return
    raise BridgeError(
        "operation_lease_lost",
        "Bridge operation no longer owns the persisted run lease",
        status_code=409,
        retryable=True,
        details={"run_id": run_id},
    )


class BridgeStoreContract(Protocol):
    """Persistence surface shared by the SQLite and PostgreSQL backends."""

    engine: str

    def create_run(self, run: BridgeRun) -> BridgeRun: ...

    def get_run(self, run_id: str) -> BridgeRun: ...

    def claim_operation(
        self,
        run_id: str,
        operation: str,
        owner_id: str,
        *,
        timeout_seconds: int,
    ) -> BridgeRun: ...

    def release_operation(self, run_id: str, owner_id: str) -> None: ...

    def renew_operation(
        self,
        run_id: str,
        owner_id: str,
        *,
        timeout_seconds: int,
    ) -> BridgeRun: ...

    def update_run(
        self,
        run: BridgeRun,
        *,
        expected_version: int,
        lease_owner: Optional[str] = None,
    ) -> BridgeRun: ...

    def append_event(
        self,
        run_id: str,
        envelope: CollaborationEnvelope,
        *,
        lease_owner: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def events(self, run_id: str) -> Dict[str, Any]: ...

    def archive_receipt(
        self,
        run_id: str,
        *,
        receipt_key: str,
        source: str,
        kind: str,
        payload: Dict[str, Any],
        lease_owner: Optional[str] = None,
    ) -> Dict[str, Any]: ...

    def receipts(self, run_id: str) -> Dict[str, Any]: ...

    def active_runs(self) -> List[BridgeRun]: ...


class BridgeStore:
    """SQLite development fallback implementing :class:`BridgeStoreContract`."""

    engine = "sqlite"

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.RLock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bridge_runs (
                    id TEXT PRIMARY KEY,
                    ego_task_id TEXT NOT NULL,
                    agentteams_project_id TEXT NOT NULL UNIQUE,
                    team TEXT NOT NULL,
                    trace_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    context_version INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    task_graph_json TEXT NOT NULL,
                    checkpoint_json TEXT NOT NULL,
                    ack_timeout_seconds INTEGER NOT NULL,
                    execution_timeout_seconds INTEGER NOT NULL,
                    max_reassignments INTEGER NOT NULL,
                    version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_bridge_runs_task
                    ON bridge_runs(ego_task_id);
                CREATE TABLE IF NOT EXISTS bridge_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    envelope_json TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES bridge_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_bridge_events_run
                    ON bridge_events(run_id, sequence);
                CREATE TRIGGER IF NOT EXISTS bridge_events_no_update
                BEFORE UPDATE ON bridge_events
                BEGIN
                    SELECT RAISE(ABORT, 'bridge events are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS bridge_events_no_delete
                BEFORE DELETE ON bridge_events
                BEGIN
                    SELECT RAISE(ABORT, 'bridge events are immutable');
                END;
                CREATE TABLE IF NOT EXISTS bridge_receipts (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    receipt_id TEXT NOT NULL UNIQUE,
                    run_id TEXT NOT NULL,
                    receipt_key TEXT NOT NULL,
                    source TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    receipt_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(run_id) REFERENCES bridge_runs(id),
                    UNIQUE(run_id, receipt_key)
                );
                CREATE INDEX IF NOT EXISTS idx_bridge_receipts_run
                    ON bridge_receipts(run_id, sequence);
                CREATE TRIGGER IF NOT EXISTS bridge_receipts_no_update
                BEFORE UPDATE ON bridge_receipts
                BEGIN
                    SELECT RAISE(ABORT, 'bridge receipts are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS bridge_receipts_no_delete
                BEFORE DELETE ON bridge_receipts
                BEGIN
                    SELECT RAISE(ABORT, 'bridge receipts are immutable');
                END;
                """
            )

    @staticmethod
    def _row_to_run(row: sqlite3.Row) -> BridgeRun:
        return BridgeRun.model_validate(
            {
                "id": row["id"],
                "ego_task_id": row["ego_task_id"],
                "agentteams_project_id": row["agentteams_project_id"],
                "team": row["team"],
                "trace_id": row["trace_id"],
                "correlation_id": row["correlation_id"],
                "context_version": row["context_version"],
                "state": row["state"],
                "mode": row["mode"],
                "objective": row["objective"],
                "task_graph": json.loads(row["task_graph_json"]),
                "checkpoint": json.loads(row["checkpoint_json"]),
                "ack_timeout_seconds": row["ack_timeout_seconds"],
                "execution_timeout_seconds": row["execution_timeout_seconds"],
                "max_reassignments": row["max_reassignments"],
                "version": row["version"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    def create_run(self, run: BridgeRun) -> BridgeRun:
        with self._lock, self._connection:
            try:
                self._connection.execute(
                    """
                    INSERT INTO bridge_runs (
                        id, ego_task_id, agentteams_project_id, team, trace_id,
                        correlation_id, context_version, state, mode, objective,
                        task_graph_json, checkpoint_json, ack_timeout_seconds,
                        execution_timeout_seconds, max_reassignments, version,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.ego_task_id,
                        run.agentteams_project_id,
                        run.team,
                        run.trace_id,
                        run.correlation_id,
                        run.context_version,
                        run.state.value,
                        run.mode,
                        run.objective,
                        canonical_json([task.model_dump(mode="json") for task in run.task_graph]),
                        canonical_json(run.checkpoint),
                        run.ack_timeout_seconds,
                        run.execution_timeout_seconds,
                        run.max_reassignments,
                        run.version,
                        run.created_at.isoformat(),
                        run.updated_at.isoformat(),
                    ),
                )
            except sqlite3.IntegrityError as error:
                raise BridgeError(
                    "run_conflict",
                    "A bridge run already owns this AgentTeams project",
                    details={"project_id": run.agentteams_project_id},
                ) from error
        return run

    def get_run(self, run_id: str) -> BridgeRun:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM bridge_runs WHERE id = ?", (run_id,)
            ).fetchone()
        if row is None:
            raise BridgeError(
                "run_not_found", "Bridge run was not found", status_code=404, details={"id": run_id}
            )
        return self._row_to_run(row)

    def claim_operation(
        self,
        run_id: str,
        operation: str,
        owner_id: str,
        *,
        timeout_seconds: int,
    ) -> BridgeRun:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                acquired_at = utc_now()
                lease = _lease_payload(operation, owner_id, acquired_at, timeout_seconds)
                row = self._connection.execute(
                    "SELECT * FROM bridge_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise BridgeError(
                        "run_not_found",
                        "Bridge run was not found",
                        status_code=404,
                        details={"id": run_id},
                    )
                checkpoint = json.loads(row["checkpoint_json"])
                _raise_if_lease_held(checkpoint, run_id=run_id, acquired_at=acquired_at)
                checkpoint[OPERATION_LEASE_KEY] = lease
                self._connection.execute(
                    "UPDATE bridge_runs SET checkpoint_json = ? WHERE id = ?",
                    (canonical_json(checkpoint), run_id),
                )
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
        payload = dict(row)
        payload["checkpoint_json"] = canonical_json(checkpoint)
        return self._row_to_run(payload)  # type: ignore[arg-type]

    def release_operation(self, run_id: str, owner_id: str) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                row = self._connection.execute(
                    "SELECT checkpoint_json FROM bridge_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    self._connection.commit()
                    return
                checkpoint = json.loads(row["checkpoint_json"])
                lease = _operation_lease(checkpoint)
                if lease is not None and lease.get("owner_id") == owner_id:
                    checkpoint.pop(OPERATION_LEASE_KEY, None)
                    self._connection.execute(
                        "UPDATE bridge_runs SET checkpoint_json = ? WHERE id = ?",
                        (canonical_json(checkpoint), run_id),
                    )
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise

    def renew_operation(
        self,
        run_id: str,
        owner_id: str,
        *,
        timeout_seconds: int,
    ) -> BridgeRun:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                renewed_at = utc_now()
                row = self._connection.execute(
                    "SELECT * FROM bridge_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if row is None:
                    raise BridgeError(
                        "run_not_found",
                        "Bridge run was not found",
                        status_code=404,
                        details={"id": run_id},
                    )
                checkpoint = json.loads(row["checkpoint_json"])
                lease = _operation_lease(checkpoint)
                _assert_update_lease(
                    checkpoint,
                    checkpoint,
                    run_id=run_id,
                    lease_owner=owner_id,
                    checked_at=renewed_at,
                )
                assert lease is not None
                checkpoint[OPERATION_LEASE_KEY] = _lease_payload(
                    str(lease["operation"]),
                    owner_id,
                    renewed_at,
                    timeout_seconds,
                )
                self._connection.execute(
                    "UPDATE bridge_runs SET checkpoint_json = ? WHERE id = ?",
                    (canonical_json(checkpoint), run_id),
                )
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
        payload = dict(row)
        payload["checkpoint_json"] = canonical_json(checkpoint)
        return self._row_to_run(payload)  # type: ignore[arg-type]

    def update_run(
        self,
        run: BridgeRun,
        *,
        expected_version: int,
        lease_owner: Optional[str] = None,
    ) -> BridgeRun:
        updated = run.model_copy(update={"version": expected_version + 1, "updated_at": utc_now()})
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._connection.execute(
                    "SELECT version, checkpoint_json FROM bridge_runs WHERE id = ?",
                    (run.id,),
                ).fetchone()
                if current is None:
                    raise BridgeError(
                        "run_not_found",
                        "Bridge run was not found",
                        status_code=404,
                        details={"id": run.id},
                    )
                if int(current["version"]) != expected_version:
                    raise BridgeError(
                        "run_version_conflict",
                        "Bridge run was concurrently modified; reload before retrying",
                        retryable=True,
                        details={"run_id": run.id, "expected_version": expected_version},
                    )
                _assert_update_lease(
                    json.loads(current["checkpoint_json"]),
                    updated.checkpoint,
                    run_id=run.id,
                    lease_owner=lease_owner,
                    checked_at=utc_now(),
                )
                self._connection.execute(
                    """
                    UPDATE bridge_runs SET
                        state = ?, task_graph_json = ?, checkpoint_json = ?, version = ?, updated_at = ?
                    WHERE id = ? AND version = ?
                    """,
                    (
                        updated.state.value,
                        canonical_json(
                            [task.model_dump(mode="json") for task in updated.task_graph]
                        ),
                        canonical_json(updated.checkpoint),
                        updated.version,
                        updated.updated_at.isoformat(),
                        updated.id,
                        expected_version,
                    ),
                )
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
        return updated

    def append_event(
        self,
        run_id: str,
        envelope: CollaborationEnvelope,
        *,
        lease_owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        envelope_payload = envelope.model_dump(mode="json", by_alias=True)
        created_at = _utc_iso(envelope.created_at)
        event_id = "evt_%s" % uuid.uuid4().hex
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                run_row = self._connection.execute(
                    "SELECT checkpoint_json FROM bridge_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if run_row is None:
                    raise BridgeError(
                        "run_not_found",
                        "Bridge run was not found",
                        status_code=404,
                        details={"id": run_id},
                    )
                checkpoint = json.loads(run_row["checkpoint_json"])
                _assert_update_lease(
                    checkpoint,
                    checkpoint,
                    run_id=run_id,
                    lease_owner=lease_owner,
                    checked_at=utc_now(),
                )
                row = self._connection.execute(
                    "SELECT event_hash FROM bridge_events "
                    "WHERE run_id = ? ORDER BY sequence DESC LIMIT 1",
                    (run_id,),
                ).fetchone()
                previous_hash = row["event_hash"] if row is not None else ZERO_HASH
                hash_payload = {
                    "event_id": event_id,
                    "run_id": run_id,
                    "kind": envelope.kind.value,
                    "envelope": envelope_payload,
                    "previous_hash": previous_hash,
                    "created_at": created_at,
                }
                event_hash = hashlib.sha256(
                    canonical_json(hash_payload).encode("utf-8")
                ).hexdigest()
                cursor = self._connection.execute(
                    """
                    INSERT INTO bridge_events (
                        event_id, run_id, kind, envelope_json, previous_hash,
                        event_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        run_id,
                        envelope.kind.value,
                        canonical_json(envelope_payload),
                        previous_hash,
                        event_hash,
                        created_at,
                    ),
                )
                if cursor.lastrowid is None:
                    raise BridgeError(
                        "event_sequence_missing",
                        "SQLite did not return an event sequence",
                    )
                sequence = cursor.lastrowid
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
        return {
            "sequence": sequence,
            "event_id": event_id,
            "run_id": run_id,
            "kind": envelope.kind.value,
            "envelope": envelope_payload,
            "previous_hash": previous_hash,
            "event_hash": event_hash,
            "created_at": created_at,
        }

    def events(self, run_id: str) -> Dict[str, Any]:
        self.get_run(run_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM bridge_events WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        items: List[Dict[str, Any]] = []
        expected_previous = ZERO_HASH
        chain_valid = True
        for row in rows:
            envelope = json.loads(row["envelope_json"])
            hash_payload = {
                "event_id": row["event_id"],
                "run_id": row["run_id"],
                "kind": row["kind"],
                "envelope": envelope,
                "previous_hash": row["previous_hash"],
                "created_at": row["created_at"],
            }
            expected_hash = hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()
            if row["previous_hash"] != expected_previous or row["event_hash"] != expected_hash:
                chain_valid = False
            expected_previous = row["event_hash"]
            items.append(
                {
                    "sequence": row["sequence"],
                    "event_id": row["event_id"],
                    "kind": row["kind"],
                    "envelope": envelope,
                    "previous_hash": row["previous_hash"],
                    "event_hash": row["event_hash"],
                    "created_at": row["created_at"],
                }
            )
        return {"items": items, "total": len(items), "chain_valid": chain_valid}

    @staticmethod
    def _receipt_row(row: sqlite3.Row, *, idempotent_replay: bool = False) -> Dict[str, Any]:
        return {
            "sequence": row["sequence"],
            "receipt_id": row["receipt_id"],
            "run_id": row["run_id"],
            "receipt_key": row["receipt_key"],
            "source": row["source"],
            "kind": row["kind"],
            "payload": json.loads(row["payload_json"]),
            "payload_sha256": row["payload_sha256"],
            "previous_hash": row["previous_hash"],
            "receipt_hash": row["receipt_hash"],
            "created_at": row["created_at"],
            "idempotent_replay": idempotent_replay,
        }

    def archive_receipt(
        self,
        run_id: str,
        *,
        receipt_key: str,
        source: str,
        kind: str,
        payload: Dict[str, Any],
        lease_owner: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Append one raw upstream receipt, replaying only byte-equivalent content.

        The public method returns ordinary dictionaries, keeping persistence consumers
        independent of SQLite row objects so another backend can implement the same surface.
        """

        payload_json = canonical_json(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                run_row = self._connection.execute(
                    "SELECT checkpoint_json FROM bridge_runs WHERE id = ?", (run_id,)
                ).fetchone()
                if run_row is None:
                    raise BridgeError(
                        "run_not_found",
                        "Bridge run was not found",
                        status_code=404,
                        details={"id": run_id},
                    )
                checkpoint = json.loads(run_row["checkpoint_json"])
                _assert_update_lease(
                    checkpoint,
                    checkpoint,
                    run_id=run_id,
                    lease_owner=lease_owner,
                    checked_at=utc_now(),
                )
                existing = self._connection.execute(
                    "SELECT * FROM bridge_receipts "
                    "WHERE run_id = ? AND receipt_key = ?",
                    (run_id, receipt_key),
                ).fetchone()
                if existing is not None:
                    if (
                        existing["payload_sha256"] != payload_sha256
                        or existing["source"] != source
                        or existing["kind"] != kind
                    ):
                        raise BridgeError(
                            "receipt_key_conflict",
                            "A receipt key was replayed with different upstream content",
                            details={"run_id": run_id, "receipt_key": receipt_key},
                        )
                    result = self._receipt_row(existing, idempotent_replay=True)
                else:
                    previous = self._connection.execute(
                        """
                        SELECT receipt_hash FROM bridge_receipts
                        WHERE run_id = ? ORDER BY sequence DESC LIMIT 1
                        """,
                        (run_id,),
                    ).fetchone()
                    previous_hash = (
                        previous["receipt_hash"] if previous is not None else ZERO_HASH
                    )
                    receipt_id = "rcpt_%s" % uuid.uuid4().hex
                    created_at = _utc_iso(utc_now())
                    hash_payload = {
                        "receipt_id": receipt_id,
                        "run_id": run_id,
                        "receipt_key": receipt_key,
                        "source": source,
                        "kind": kind,
                        "payload_sha256": payload_sha256,
                        "previous_hash": previous_hash,
                        "created_at": created_at,
                    }
                    receipt_hash = hashlib.sha256(
                        canonical_json(hash_payload).encode("utf-8")
                    ).hexdigest()
                    self._connection.execute(
                        """
                        INSERT INTO bridge_receipts(
                            receipt_id, run_id, receipt_key, source, kind, payload_json,
                            payload_sha256, previous_hash, receipt_hash, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            receipt_id,
                            run_id,
                            receipt_key,
                            source,
                            kind,
                            payload_json,
                            payload_sha256,
                            previous_hash,
                            receipt_hash,
                            created_at,
                        ),
                    )
                    row = self._connection.execute(
                        "SELECT * FROM bridge_receipts WHERE receipt_id = ?",
                        (receipt_id,),
                    ).fetchone()
                    if row is None:
                        raise BridgeError(
                            "receipt_missing",
                            "Archived receipt could not be reloaded",
                        )
                    result = self._receipt_row(row)
                self._connection.commit()
            except Exception:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
        return result

    def receipts(self, run_id: str) -> Dict[str, Any]:
        self.get_run(run_id)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM bridge_receipts WHERE run_id = ? ORDER BY sequence", (run_id,)
            ).fetchall()
        items: List[Dict[str, Any]] = []
        expected_previous = ZERO_HASH
        chain_valid = True
        for row in rows:
            item = self._receipt_row(row)
            hash_payload = {
                "receipt_id": item["receipt_id"],
                "run_id": item["run_id"],
                "receipt_key": item["receipt_key"],
                "source": item["source"],
                "kind": item["kind"],
                "payload_sha256": item["payload_sha256"],
                "previous_hash": item["previous_hash"],
                "created_at": item["created_at"],
            }
            expected_hash = hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()
            expected_payload_sha256 = hashlib.sha256(
                canonical_json(item["payload"]).encode("utf-8")
            ).hexdigest()
            if (
                item["payload_sha256"] != expected_payload_sha256
                or item["previous_hash"] != expected_previous
                or item["receipt_hash"] != expected_hash
            ):
                chain_valid = False
            expected_previous = item["receipt_hash"]
            items.append(item)
        return {"items": items, "total": len(items), "chain_valid": chain_valid}

    def active_runs(self) -> List[BridgeRun]:
        terminal = (RunState.BLOCKED.value, RunState.COMPLETED.value)
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM bridge_runs WHERE state NOT IN (?, ?) ORDER BY created_at",
                terminal,
            ).fetchall()
        return [self._row_to_run(row) for row in rows]


def build_bridge_store(
    *,
    database_url: str = "",
    migration_database_url: str = "",
    migration_mode: str = "",
    sqlite_path: str = "/tmp/egoagentos-agentteams-bridge.sqlite3",
) -> BridgeStoreContract:
    """Select PostgreSQL only when its dedicated bridge URL is explicit.

    A malformed or unavailable PostgreSQL target fails closed. It never falls back to
    SQLite after the operator supplied ``EGO_AGENTTEAMS_DATABASE_URL``.
    """

    if database_url.strip():
        from .postgres_store import PostgresBridgeStore

        return PostgresBridgeStore(
            database_url.strip(),
            migration_database_url=migration_database_url.strip(),
            migration_mode=migration_mode.strip() or None,
        )
    return BridgeStore(sqlite_path)
