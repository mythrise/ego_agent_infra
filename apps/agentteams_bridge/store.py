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

from benchmarks.secure_memory.canonical import canonical_bytes, parse_json_bytes
from benchmarks.secure_memory.models import SignedTaskLease

from .extensions import (
    AttentionPacket,
    CampaignBinding,
    CanonicalEffect,
    GuardianDecision,
    RiskAssessment,
    RiskStage,
    SafetyDecision,
    UserStatusProjection,
)
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

    def bind_campaign(self, run_id: str, binding: CampaignBinding) -> Dict[str, Any]: ...

    def campaign_binding(
        self,
        run_id: str,
        *,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]: ...

    def append_extension_event(
        self,
        run_id: str,
        *,
        event_type: str,
        event: Any,
        idempotency_key: str,
        memory_watermark: int,
    ) -> Dict[str, Any]: ...

    def extension_events(
        self,
        run_id: str,
        *,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]: ...

    def verify_extension_root(
        self,
        run_id: str,
        *,
        expected_root_hash: str,
        project_id: str,
        configuration_id: Optional[str],
    ) -> bool: ...

    def store_signed_task_lease(
        self,
        run_id: str,
        *,
        already_verified_signed_payload: bytes,
        idempotency_key: str,
    ) -> Dict[str, Any]: ...

    def task_lease(
        self,
        run_id: str,
        *,
        task_id: str,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]: ...

    def bind_sealed_evaluation(
        self,
        run_id: str,
        *,
        binding_id: str,
        task_id: str,
        already_verified_signed_payload: bytes,
        signature_base64: str,
        key_id: str,
        issuer_id: str,
        idempotency_key: str,
    ) -> Dict[str, Any]: ...

    def evaluator_binding(
        self,
        run_id: str,
        *,
        binding_id: str,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]: ...

    def replay_extension_authority(
        self,
        run_id: str,
        *,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]: ...


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
                CREATE TABLE IF NOT EXISTS bridge_extension_events (
                    run_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL CHECK(sequence > 0),
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    canonical_payload BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    memory_watermark INTEGER NOT NULL CHECK(memory_watermark >= 0),
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, sequence),
                    UNIQUE(run_id, idempotency_key),
                    FOREIGN KEY(run_id) REFERENCES bridge_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_bridge_extension_events_run
                    ON bridge_extension_events(run_id, sequence);
                CREATE TRIGGER IF NOT EXISTS bridge_extension_events_no_update
                BEFORE UPDATE ON bridge_extension_events
                BEGIN
                    SELECT RAISE(ABORT, 'bridge extension events are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS bridge_extension_events_no_delete
                BEFORE DELETE ON bridge_extension_events
                BEGIN
                    SELECT RAISE(ABORT, 'bridge extension events are immutable');
                END;
                CREATE TABLE IF NOT EXISTS bridge_task_leases (
                    run_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    event_sequence INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    canonical_signed_payload BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    signature_base64 TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    issuer_id TEXT NOT NULL,
                    previous_stream_digest TEXT NOT NULL,
                    stream_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, task_id),
                    UNIQUE(run_id, event_sequence),
                    UNIQUE(run_id, idempotency_key),
                    FOREIGN KEY(run_id, event_sequence)
                        REFERENCES bridge_extension_events(run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS bridge_evaluator_bindings (
                    run_id TEXT NOT NULL,
                    binding_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    event_sequence INTEGER NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    canonical_signed_payload BLOB NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    signature_base64 TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    issuer_id TEXT NOT NULL,
                    previous_stream_digest TEXT NOT NULL,
                    stream_digest TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(run_id, binding_id),
                    UNIQUE(run_id, event_sequence),
                    UNIQUE(run_id, idempotency_key),
                    FOREIGN KEY(run_id, task_id) REFERENCES bridge_task_leases(run_id, task_id),
                    FOREIGN KEY(run_id, event_sequence)
                        REFERENCES bridge_extension_events(run_id, sequence)
                );
                CREATE TRIGGER IF NOT EXISTS bridge_task_leases_no_update
                BEFORE UPDATE ON bridge_task_leases
                BEGIN
                    SELECT RAISE(ABORT, 'bridge task leases are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS bridge_task_leases_no_delete
                BEFORE DELETE ON bridge_task_leases
                BEGIN
                    SELECT RAISE(ABORT, 'bridge task leases are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS bridge_evaluator_bindings_no_update
                BEFORE UPDATE ON bridge_evaluator_bindings
                BEGIN
                    SELECT RAISE(ABORT, 'bridge evaluator bindings are immutable');
                END;
                CREATE TRIGGER IF NOT EXISTS bridge_evaluator_bindings_no_delete
                BEFORE DELETE ON bridge_evaluator_bindings
                BEGIN
                    SELECT RAISE(ABORT, 'bridge evaluator bindings are immutable');
                END;
                """
            )
            self._ensure_campaign_columns()
            self._connection.executescript(
                """
                CREATE TRIGGER IF NOT EXISTS bridge_runs_campaign_no_update
                BEFORE UPDATE ON bridge_runs
                WHEN OLD.campaign_binding_json IS NOT NULL AND (
                    NEW.campaign_binding_json IS NOT OLD.campaign_binding_json OR
                    NEW.campaign_binding_sha256 IS NOT OLD.campaign_binding_sha256 OR
                    NEW.campaign_id IS NOT OLD.campaign_id OR
                    NEW.configuration_id IS NOT OLD.configuration_id OR
                    NEW.execution_phase_owner IS NOT OLD.execution_phase_owner OR
                    NEW.problem_id IS NOT OLD.problem_id OR
                    NEW.campaign_turn IS NOT OLD.campaign_turn OR
                    NEW.campaign_generation IS NOT OLD.campaign_generation OR
                    NEW.manifest_sha256 IS NOT OLD.manifest_sha256 OR
                    NEW.post_selection_extension_sha256 IS NOT OLD.post_selection_extension_sha256 OR
                    NEW.policy_sha256 IS NOT OLD.policy_sha256 OR
                    NEW.requirement_ledger_sha256 IS NOT OLD.requirement_ledger_sha256 OR
                    NEW.workspace_checkpoint_sha256 IS NOT OLD.workspace_checkpoint_sha256 OR
                    NEW.memory_watermark IS NOT OLD.memory_watermark
                )
                BEGIN
                    SELECT RAISE(ABORT, 'bridge campaign binding is immutable');
                END;
                """
            )

    def _ensure_campaign_columns(self) -> None:
        existing = {
            str(row["name"])
            for row in self._connection.execute("PRAGMA table_info(bridge_runs)").fetchall()
        }
        columns = {
            "campaign_binding_json": "TEXT",
            "campaign_binding_sha256": "TEXT",
            "campaign_id": "TEXT",
            "configuration_id": "TEXT",
            "execution_phase_owner": "TEXT",
            "problem_id": "TEXT",
            "campaign_turn": "INTEGER",
            "campaign_generation": "INTEGER",
            "manifest_sha256": "TEXT",
            "post_selection_extension_sha256": "TEXT",
            "policy_sha256": "TEXT",
            "requirement_ledger_sha256": "TEXT",
            "workspace_checkpoint_sha256": "TEXT",
            "memory_watermark": "INTEGER",
        }
        for name, sql_type in columns.items():
            if name not in existing:
                self._connection.execute(
                    "ALTER TABLE bridge_runs ADD COLUMN %s %s" % (name, sql_type)
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

    @staticmethod
    def _campaign_row(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "run_id": row["id"],
            "project_id": row["agentteams_project_id"],
            "binding": json.loads(row["campaign_binding_json"]),
            "binding_sha256": row["campaign_binding_sha256"],
        }

    def bind_campaign(self, run_id: str, binding: CampaignBinding) -> Dict[str, Any]:
        payload = binding.model_dump(mode="json")
        payload_json = canonical_json(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._lock, self._connection:
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
            if row["campaign_binding_json"] is not None:
                if row["campaign_binding_json"] != payload_json:
                    raise BridgeError(
                        "campaign_binding_conflict",
                        "Bridge run already has a different immutable campaign binding",
                        details={"run_id": run_id},
                    )
                return self._campaign_row(row)
            self._connection.execute(
                """
                UPDATE bridge_runs SET
                    campaign_binding_json = ?, campaign_binding_sha256 = ?,
                    campaign_id = ?, configuration_id = ?, execution_phase_owner = ?,
                    problem_id = ?, campaign_turn = ?, campaign_generation = ?,
                    manifest_sha256 = ?, post_selection_extension_sha256 = ?,
                    policy_sha256 = ?, requirement_ledger_sha256 = ?,
                    workspace_checkpoint_sha256 = ?, memory_watermark = ?
                WHERE id = ? AND campaign_binding_json IS NULL
                """,
                (
                    payload_json,
                    payload_sha256,
                    binding.campaign_id,
                    binding.configuration_id.value if binding.configuration_id else None,
                    binding.execution_phase_owner.value,
                    binding.problem_id,
                    binding.turn,
                    binding.generation,
                    binding.manifest_sha256,
                    binding.post_selection_extension_sha256,
                    binding.policy_sha256,
                    binding.requirement_ledger_sha256,
                    binding.workspace_checkpoint_sha256,
                    binding.memory_watermark,
                    run_id,
                ),
            )
            stored = self._connection.execute(
                "SELECT * FROM bridge_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if stored is None:  # pragma: no cover - transaction-local invariant
                raise BridgeError("campaign_binding_missing", "Campaign binding was not stored")
            return self._campaign_row(stored)

    def campaign_binding(
        self,
        run_id: str,
        *,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]:
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM bridge_runs
                WHERE id = ? AND agentteams_project_id = ?
                  AND configuration_id IS ? AND campaign_binding_json IS NOT NULL
                """,
                (run_id, project_id, configuration_id),
            ).fetchone()
        if row is None:
            raise BridgeError(
                "campaign_binding_not_found",
                "No campaign binding matches this run, project, and configuration",
                status_code=404,
                details={"run_id": run_id, "project_id": project_id},
            )
        return self._campaign_row(row)

    @staticmethod
    def _typed_extension_payload(event_type: str, event: Any) -> Dict[str, Any]:
        event_models: Dict[str, Any] = {
            "CANONICAL_EFFECT": CanonicalEffect,
            "SYSTEM_RISK_ASSESSMENT": RiskAssessment,
            "GUARDIAN_DECISION": GuardianDecision,
            "SAFETY_DECISION": SafetyDecision,
            "ATTENTION_PACKET": AttentionPacket,
            "USER_STATUS_PROJECTION": UserStatusProjection,
        }
        model = event_models.get(event_type)
        if model is None:
            raise BridgeError(
                "extension_event_type_invalid",
                "Extension event type is not a frozen typed contract",
                details={"event_type": event_type},
            )
        try:
            validated = model.model_validate(event)
        except (TypeError, ValueError) as error:
            raise BridgeError(
                "extension_event_invalid",
                "Extension event does not satisfy its frozen typed contract",
                details={"event_type": event_type},
            ) from error
        if (
            event_type == "SYSTEM_RISK_ASSESSMENT"
            and validated.stage is not RiskStage.SYSTEM
        ):
            raise BridgeError(
                "extension_event_invalid",
                "System risk event must use the SYSTEM assessment stage",
            )
        return validated.model_dump(mode="json")

    @staticmethod
    def _extension_row(
        row: sqlite3.Row, *, idempotent_replay: bool = False
    ) -> Dict[str, Any]:
        raw = bytes(row["canonical_payload"])
        return {
            "sequence": row["sequence"],
            "event_id": row["event_id"],
            "run_id": row["run_id"],
            "event_type": row["event_type"],
            "idempotency_key": row["idempotency_key"],
            "event": json.loads(raw.decode("utf-8")),
            "canonical_payload": raw,
            "payload_sha256": row["payload_sha256"],
            "memory_watermark": row["memory_watermark"],
            "previous_hash": row["previous_hash"],
            "event_hash": row["event_hash"],
            "created_at": row["created_at"],
            "idempotent_replay": idempotent_replay,
        }

    def append_extension_event(
        self,
        run_id: str,
        *,
        event_type: str,
        event: Any,
        idempotency_key: str,
        memory_watermark: int,
    ) -> Dict[str, Any]:
        if not idempotency_key:
            raise BridgeError("idempotency_key_invalid", "Idempotency key must be non-empty")
        payload = self._typed_extension_payload(event_type, event)
        canonical_payload = canonical_json(payload).encode("utf-8")
        with self._lock, self._connection:
            return self._append_extension_bytes_locked(
                run_id,
                event_type=event_type,
                canonical_payload=canonical_payload,
                idempotency_key=idempotency_key,
                memory_watermark=memory_watermark,
            )

    def _append_extension_bytes_locked(
        self,
        run_id: str,
        *,
        event_type: str,
        canonical_payload: bytes,
        idempotency_key: str,
        memory_watermark: int,
    ) -> Dict[str, Any]:
        payload_sha256 = hashlib.sha256(canonical_payload).hexdigest()
        run = self._connection.execute(
            "SELECT * FROM bridge_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if run is None:
            raise BridgeError("run_not_found", "Bridge run was not found", status_code=404)
        if run["campaign_binding_json"] is None:
            raise BridgeError(
                "campaign_binding_required",
                "Extension events require an immutable campaign binding",
            )
        if memory_watermark > int(run["memory_watermark"]):
            raise BridgeError(
                "future_memory_watermark",
                "Extension event cannot cite a future memory watermark",
            )
        existing = self._connection.execute(
            """
            SELECT * FROM bridge_extension_events
            WHERE run_id = ? AND idempotency_key = ?
            """,
            (run_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if (
                bytes(existing["canonical_payload"]) != canonical_payload
                or existing["event_type"] != event_type
                or existing["memory_watermark"] != memory_watermark
            ):
                raise BridgeError(
                    "idempotency_conflict",
                    "Idempotency key was replayed with different canonical bytes",
                    details={"run_id": run_id, "idempotency_key": idempotency_key},
                )
            return self._extension_row(existing, idempotent_replay=True)
        payload = json.loads(canonical_payload.decode("utf-8"))
        self._validate_extension_admission_locked(
            run,
            event_type=event_type,
            payload=payload,
            memory_watermark=memory_watermark,
        )
        previous = self._connection.execute(
            """
            SELECT sequence, event_hash FROM bridge_extension_events
            WHERE run_id = ? ORDER BY sequence DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        sequence = int(previous["sequence"]) + 1 if previous is not None else 1
        previous_hash = previous["event_hash"] if previous is not None else ZERO_HASH
        event_id = "xevt_%s" % uuid.uuid4().hex
        created_at = _utc_iso(utc_now())
        hash_payload = {
            "sequence": sequence,
            "event_id": event_id,
            "run_id": run_id,
            "event_type": event_type,
            "idempotency_key": idempotency_key,
            "payload_sha256": payload_sha256,
            "memory_watermark": memory_watermark,
            "previous_hash": previous_hash,
            "created_at": created_at,
        }
        event_hash = hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()
        self._connection.execute(
            """
            INSERT INTO bridge_extension_events(
                run_id, sequence, event_id, event_type, idempotency_key,
                canonical_payload, payload_sha256, memory_watermark,
                previous_hash, event_hash, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                sequence,
                event_id,
                event_type,
                idempotency_key,
                canonical_payload,
                payload_sha256,
                memory_watermark,
                previous_hash,
                event_hash,
                created_at,
            ),
        )
        row = self._connection.execute(
            "SELECT * FROM bridge_extension_events WHERE event_id = ?", (event_id,)
        ).fetchone()
        if row is None:  # pragma: no cover - transaction-local invariant
            raise BridgeError("extension_event_missing", "Extension event was not stored")
        return self._extension_row(row)

    def _has_exact_extension_event_locked(
        self, run_id: str, *, event_type: str, payload: Dict[str, Any]
    ) -> bool:
        row = self._connection.execute(
            """
            SELECT 1 FROM bridge_extension_events
            WHERE run_id = ? AND event_type = ? AND canonical_payload = ?
            """,
            (run_id, event_type, canonical_json(payload).encode("utf-8")),
        ).fetchone()
        return row is not None

    def _require_task_lease_locked(self, run_id: str, task_id: str) -> None:
        row = self._connection.execute(
            "SELECT 1 FROM bridge_task_leases WHERE run_id = ? AND task_id = ?",
            (run_id, task_id),
        ).fetchone()
        if row is None:
            raise BridgeError(
                "task_lease_required",
                "Extension evidence requires the exact admitted task lease",
            )

    def _validate_extension_admission_locked(
        self,
        run: sqlite3.Row,
        *,
        event_type: str,
        payload: Dict[str, Any],
        memory_watermark: int,
    ) -> None:
        run_id = str(run["id"])
        binding = json.loads(run["campaign_binding_json"])
        if event_type == "CANONICAL_EFFECT":
            if (
                payload["project_id"] != run["agentteams_project_id"]
                or payload["workspace_checkpoint_sha256"]
                != binding["workspace_checkpoint_sha256"]
                or payload["policy_sha256"] != binding["policy_sha256"]
            ):
                raise BridgeError(
                    "effect_binding_mismatch",
                    "Canonical effect does not match the bound project, checkpoint, and policy",
                )
            self._require_task_lease_locked(run_id, payload["task_id"])
        elif event_type == "GUARDIAN_DECISION":
            system = payload["system_assessment"]
            if system["risk_level"] != "HIGH" or not self._has_exact_extension_event_locked(
                run_id,
                event_type="SYSTEM_RISK_ASSESSMENT",
                payload=system,
            ):
                raise BridgeError(
                    "guardian_order_invalid",
                    "Guardian decision requires its matching admitted system-HIGH assessment",
                )
        elif event_type == "SAFETY_DECISION":
            effect = payload["effect"]
            guardian = payload["guardian_decision"]
            if not self._has_exact_extension_event_locked(
                run_id, event_type="CANONICAL_EFFECT", payload=effect
            ) or not self._has_exact_extension_event_locked(
                run_id, event_type="GUARDIAN_DECISION", payload=guardian
            ):
                raise BridgeError(
                    "safety_binding_unadmitted",
                    "Safety decision requires its exact admitted effect and Guardian decision",
                )
        elif event_type == "ATTENTION_PACKET":
            if (
                payload["project_id"] != run["agentteams_project_id"]
                or payload["turn"] != binding["turn"]
                or payload["generation"] != binding["generation"]
                or payload["requirement_ledger_sha256"]
                != binding["requirement_ledger_sha256"]
                or payload["workspace_checkpoint_sha256"]
                != binding["workspace_checkpoint_sha256"]
                or payload["policy_sha256"] != binding["policy_sha256"]
                or payload["memory_watermark"] != memory_watermark
            ):
                raise BridgeError(
                    "attention_binding_mismatch",
                    "Attention packet does not match the immutable campaign authority",
                )
            self._require_task_lease_locked(run_id, payload["task_id"])
        elif event_type == "USER_STATUS_PROJECTION":
            if payload["project_id"] != run["agentteams_project_id"]:
                raise BridgeError(
                    "projection_binding_mismatch",
                    "User projection does not match the bound bridge project",
                )
            self._require_task_lease_locked(run_id, payload["task_id"])
            source_ids = payload["source_event_ids"]
            if source_ids:
                placeholders = ",".join("?" for _ in source_ids)
                parameters = (run_id,) + tuple(source_ids)
                admitted = self._connection.execute(
                    """
                    SELECT count(*) AS total FROM bridge_extension_events
                    WHERE run_id = ? AND event_id IN (%s)
                    """ % placeholders,
                    parameters,
                ).fetchone()
                if admitted is None or int(admitted["total"]) != len(source_ids):
                    raise BridgeError(
                        "projection_source_unadmitted",
                        "User projection cites a future or unadmitted extension event",
                    )
            guardian = payload.get("guardian_decision")
            safety = payload.get("safety_decision")
            if guardian is not None and not self._has_exact_extension_event_locked(
                run_id, event_type="GUARDIAN_DECISION", payload=guardian
            ):
                raise BridgeError(
                    "projection_source_unadmitted",
                    "User projection embeds an unadmitted Guardian decision",
                )
            if safety is not None and not self._has_exact_extension_event_locked(
                run_id, event_type="SAFETY_DECISION", payload=safety
            ):
                raise BridgeError(
                    "projection_source_unadmitted",
                    "User projection embeds an unadmitted Safety decision",
                )

    def extension_events(
        self,
        run_id: str,
        *,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]:
        self.campaign_binding(
            run_id,
            project_id=project_id,
            configuration_id=configuration_id,
        )
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM bridge_extension_events
                WHERE run_id = ? ORDER BY sequence
                """,
                (run_id,),
            ).fetchall()
        items: List[Dict[str, Any]] = []
        expected_previous = ZERO_HASH
        chain_valid = True
        for row in rows:
            item = self._extension_row(row)
            expected_payload_sha256 = hashlib.sha256(item["canonical_payload"]).hexdigest()
            hash_payload = {
                "sequence": item["sequence"],
                "event_id": item["event_id"],
                "run_id": item["run_id"],
                "event_type": item["event_type"],
                "idempotency_key": item["idempotency_key"],
                "payload_sha256": item["payload_sha256"],
                "memory_watermark": item["memory_watermark"],
                "previous_hash": item["previous_hash"],
                "created_at": item["created_at"],
            }
            expected_hash = hashlib.sha256(
                canonical_json(hash_payload).encode("utf-8")
            ).hexdigest()
            if (
                item["payload_sha256"] != expected_payload_sha256
                or item["previous_hash"] != expected_previous
                or item["event_hash"] != expected_hash
            ):
                chain_valid = False
            expected_previous = item["event_hash"]
            items.append(item)
        return {
            "items": items,
            "total": len(items),
            "chain_valid": chain_valid,
            "root_hash": expected_previous,
        }

    def verify_extension_root(
        self,
        run_id: str,
        *,
        expected_root_hash: str,
        project_id: str,
        configuration_id: Optional[str],
    ) -> bool:
        replay = self.extension_events(
            run_id,
            project_id=project_id,
            configuration_id=configuration_id,
        )
        return bool(
            replay["chain_valid"] and replay["root_hash"] == expected_root_hash
        )

    @staticmethod
    def _lease_row(
        row: sqlite3.Row, *, idempotent_replay: bool = False
    ) -> Dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "task_id": row["task_id"],
            "event_sequence": row["event_sequence"],
            "idempotency_key": row["idempotency_key"],
            "canonical_signed_payload": bytes(row["canonical_signed_payload"]),
            "payload_sha256": row["payload_sha256"],
            "signature_base64": row["signature_base64"],
            "key_id": row["key_id"],
            "issuer_id": row["issuer_id"],
            "previous_stream_digest": row["previous_stream_digest"],
            "stream_digest": row["stream_digest"],
            "created_at": row["created_at"],
            "idempotent_replay": idempotent_replay,
        }

    @staticmethod
    def _evaluator_row(
        row: sqlite3.Row, *, idempotent_replay: bool = False
    ) -> Dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "binding_id": row["binding_id"],
            "task_id": row["task_id"],
            "event_sequence": row["event_sequence"],
            "idempotency_key": row["idempotency_key"],
            "canonical_signed_payload": bytes(row["canonical_signed_payload"]),
            "payload_sha256": row["payload_sha256"],
            "signature_base64": row["signature_base64"],
            "key_id": row["key_id"],
            "issuer_id": row["issuer_id"],
            "previous_stream_digest": row["previous_stream_digest"],
            "stream_digest": row["stream_digest"],
            "created_at": row["created_at"],
            "idempotent_replay": idempotent_replay,
        }

    @staticmethod
    def _require_canonical_object(raw: bytes) -> Dict[str, Any]:
        if not isinstance(raw, bytes):
            raise BridgeError(
                "verified_payload_invalid",
                "Already-verified signed payload must be exact canonical bytes",
            )
        try:
            payload = parse_json_bytes(raw)
        except (TypeError, ValueError) as error:
            raise BridgeError(
                "verified_payload_invalid",
                "Already-verified signed payload is not valid canonical JSON",
            ) from error
        if not isinstance(payload, dict) or canonical_bytes(payload) != raw:
            raise BridgeError(
                "verified_payload_invalid",
                "Already-verified signed payload must be one canonical JSON object",
            )
        return payload

    @staticmethod
    def _assert_lease_binding(run: sqlite3.Row, lease: SignedTaskLease) -> None:
        binding = json.loads(run["campaign_binding_json"])
        core = lease.core.model_dump(mode="json")
        field_names = (
            "campaign_id",
            "configuration_id",
            "execution_phase_owner",
            "problem_id",
            "turn",
            "generation",
            "manifest_sha256",
            "post_selection_extension_sha256",
            "policy_sha256",
            "requirement_ledger_sha256",
            "workspace_checkpoint_sha256",
            "memory_watermark",
        )
        if any(core[name] != binding[name] for name in field_names):
            raise BridgeError(
                "task_lease_binding_mismatch",
                "Already-verified task lease does not match the immutable campaign binding",
            )
        if core["project_id"] != run["agentteams_project_id"]:
            raise BridgeError(
                "task_lease_binding_mismatch",
                "Already-verified task lease does not match the bridge project",
            )

    def store_signed_task_lease(
        self,
        run_id: str,
        *,
        already_verified_signed_payload: bytes,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        payload = self._require_canonical_object(already_verified_signed_payload)
        try:
            lease = SignedTaskLease.model_validate(payload)
        except (TypeError, ValueError) as error:
            raise BridgeError(
                "verified_task_lease_invalid",
                "Already-verified payload is not a valid signed task lease",
            ) from error
        with self._lock, self._connection:
            run = self._connection.execute(
                "SELECT * FROM bridge_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise BridgeError("run_not_found", "Bridge run was not found", status_code=404)
            if run["campaign_binding_json"] is None:
                raise BridgeError(
                    "campaign_binding_required",
                    "Task lease admission requires an immutable campaign binding",
                )
            self._assert_lease_binding(run, lease)
            event = self._append_extension_bytes_locked(
                run_id,
                event_type="VERIFIED_TASK_LEASE_ADMISSION",
                canonical_payload=already_verified_signed_payload,
                idempotency_key=idempotency_key,
                memory_watermark=lease.core.memory_watermark,
            )
            existing = self._connection.execute(
                """
                SELECT * FROM bridge_task_leases
                WHERE run_id = ? AND task_id = ?
                """,
                (run_id, lease.core.task_id),
            ).fetchone()
            if existing is not None:
                if existing["event_sequence"] != event["sequence"]:
                    raise BridgeError(
                        "task_lease_conflict",
                        "Task already has a different immutable lease admission",
                    )
                return self._lease_row(existing, idempotent_replay=True)
            self._connection.execute(
                """
                INSERT INTO bridge_task_leases(
                    run_id, task_id, event_sequence, idempotency_key,
                    canonical_signed_payload, payload_sha256, signature_base64,
                    key_id, issuer_id, previous_stream_digest, stream_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    lease.core.task_id,
                    event["sequence"],
                    idempotency_key,
                    already_verified_signed_payload,
                    event["payload_sha256"],
                    lease.signature_base64,
                    lease.core.key_id,
                    lease.core.issuer_id,
                    event["previous_hash"],
                    event["event_hash"],
                    event["created_at"],
                ),
            )
            row = self._connection.execute(
                """
                SELECT * FROM bridge_task_leases
                WHERE run_id = ? AND task_id = ?
                """,
                (run_id, lease.core.task_id),
            ).fetchone()
            if row is None:  # pragma: no cover - transaction-local invariant
                raise BridgeError("task_lease_missing", "Task lease admission was not stored")
            return self._lease_row(row)

    def task_lease(
        self,
        run_id: str,
        *,
        task_id: str,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]:
        self.campaign_binding(
            run_id, project_id=project_id, configuration_id=configuration_id
        )
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM bridge_task_leases WHERE run_id = ? AND task_id = ?",
                (run_id, task_id),
            ).fetchone()
        if row is None:
            raise BridgeError(
                "task_lease_not_found",
                "No admitted task lease matches this run and task",
                status_code=404,
            )
        return self._lease_row(row)

    @classmethod
    def _reject_private_verification_material(cls, value: Any) -> None:
        forbidden = {"bearer", "dsn", "hidden", "private", "prompt", "raw_key", "secret", "token"}
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                parts = set(normalized.split("_"))
                if normalized in forbidden or parts.intersection(forbidden):
                    raise BridgeError(
                        "verified_payload_forbidden_material",
                        "Evaluator binding contains forbidden private or bearer material",
                    )
                cls._reject_private_verification_material(item)
        elif isinstance(value, list):
            for item in value:
                cls._reject_private_verification_material(item)

    def bind_sealed_evaluation(
        self,
        run_id: str,
        *,
        binding_id: str,
        task_id: str,
        already_verified_signed_payload: bytes,
        signature_base64: str,
        key_id: str,
        issuer_id: str,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        if not all((binding_id, task_id, signature_base64, key_id, issuer_id, idempotency_key)):
            raise BridgeError(
                "verified_evaluator_binding_invalid",
                "Evaluator admission public metadata must be non-empty",
            )
        payload = self._require_canonical_object(already_verified_signed_payload)
        self._reject_private_verification_material(payload)
        if payload.get("task_id") != task_id:
            raise BridgeError(
                "verified_evaluator_binding_invalid",
                "Evaluator admission task does not match its signed payload",
            )
        with self._lock, self._connection:
            run = self._connection.execute(
                "SELECT * FROM bridge_runs WHERE id = ?", (run_id,)
            ).fetchone()
            if run is None:
                raise BridgeError("run_not_found", "Bridge run was not found", status_code=404)
            if run["campaign_binding_json"] is None:
                raise BridgeError(
                    "campaign_binding_required",
                    "Evaluator admission requires an immutable campaign binding",
                )
            lease = self._connection.execute(
                "SELECT 1 FROM bridge_task_leases WHERE run_id = ? AND task_id = ?",
                (run_id, task_id),
            ).fetchone()
            if lease is None:
                raise BridgeError(
                    "task_lease_required",
                    "Evaluator admission requires the exact admitted task lease",
                )
            event = self._append_extension_bytes_locked(
                run_id,
                event_type="VERIFIED_EVALUATOR_ADMISSION",
                canonical_payload=already_verified_signed_payload,
                idempotency_key=idempotency_key,
                memory_watermark=int(run["memory_watermark"]),
            )
            existing = self._connection.execute(
                """
                SELECT * FROM bridge_evaluator_bindings
                WHERE run_id = ? AND binding_id = ?
                """,
                (run_id, binding_id),
            ).fetchone()
            if existing is not None:
                if existing["event_sequence"] != event["sequence"]:
                    raise BridgeError(
                        "evaluator_binding_conflict",
                        "Evaluator binding ID already has different immutable evidence",
                    )
                return self._evaluator_row(existing, idempotent_replay=True)
            self._connection.execute(
                """
                INSERT INTO bridge_evaluator_bindings(
                    run_id, binding_id, task_id, event_sequence, idempotency_key,
                    canonical_signed_payload, payload_sha256, signature_base64,
                    key_id, issuer_id, previous_stream_digest, stream_digest, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    binding_id,
                    task_id,
                    event["sequence"],
                    idempotency_key,
                    already_verified_signed_payload,
                    event["payload_sha256"],
                    signature_base64,
                    key_id,
                    issuer_id,
                    event["previous_hash"],
                    event["event_hash"],
                    event["created_at"],
                ),
            )
            row = self._connection.execute(
                """
                SELECT * FROM bridge_evaluator_bindings
                WHERE run_id = ? AND binding_id = ?
                """,
                (run_id, binding_id),
            ).fetchone()
            if row is None:  # pragma: no cover - transaction-local invariant
                raise BridgeError(
                    "evaluator_binding_missing", "Evaluator admission was not stored"
                )
            return self._evaluator_row(row)

    def evaluator_binding(
        self,
        run_id: str,
        *,
        binding_id: str,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]:
        self.campaign_binding(
            run_id, project_id=project_id, configuration_id=configuration_id
        )
        with self._lock:
            row = self._connection.execute(
                """
                SELECT * FROM bridge_evaluator_bindings
                WHERE run_id = ? AND binding_id = ?
                """,
                (run_id, binding_id),
            ).fetchone()
        if row is None:
            raise BridgeError(
                "evaluator_binding_not_found",
                "No evaluator admission matches this run and binding ID",
                status_code=404,
            )
        return self._evaluator_row(row)

    def replay_extension_authority(
        self,
        run_id: str,
        *,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]:
        campaign = self.campaign_binding(
            run_id, project_id=project_id, configuration_id=configuration_id
        )
        events = self.extension_events(
            run_id, project_id=project_id, configuration_id=configuration_id
        )
        with self._lock:
            lease_rows = self._connection.execute(
                """
                SELECT * FROM bridge_task_leases
                WHERE run_id = ? ORDER BY event_sequence
                """,
                (run_id,),
            ).fetchall()
            evaluator_rows = self._connection.execute(
                """
                SELECT * FROM bridge_evaluator_bindings
                WHERE run_id = ? ORDER BY event_sequence
                """,
                (run_id,),
            ).fetchall()
        items = events["items"]
        guardian_decisions = [
            item for item in items if item["event_type"] == "GUARDIAN_DECISION"
        ]
        safety_decisions = [
            item for item in items if item["event_type"] == "SAFETY_DECISION"
        ]
        projections = [
            item for item in items if item["event_type"] == "USER_STATUS_PROJECTION"
        ]
        return {
            "campaign_binding": campaign,
            "task_leases": [self._lease_row(row) for row in lease_rows],
            "evaluator_bindings": [self._evaluator_row(row) for row in evaluator_rows],
            "guardian_decisions": guardian_decisions,
            "safety_decisions": safety_decisions,
            "projection": projections[-1] if projections else None,
            "events": events,
        }

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
