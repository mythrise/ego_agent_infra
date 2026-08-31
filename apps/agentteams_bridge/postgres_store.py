"""PostgreSQL/PolarDB-PG persistence for the live AgentTeams bridge."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from datetime import datetime
from importlib import resources
from typing import Any, Dict, Iterator, List, Mapping, Optional

import psycopg
from psycopg import Connection
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from benchmarks.secure_memory.models import SignedTaskLease

from .extensions import CampaignBinding
from .errors import BridgeError
from .models import BridgeRun, CollaborationEnvelope, RunState, canonical_json, utc_now
from .store import (
    BridgeStore,
    OPERATION_LEASE_KEY,
    ZERO_HASH,
    _assert_update_lease,
    _lease_payload,
    _operation_lease,
    _raise_if_lease_held,
    _utc_iso,
)


class PostgresBridgeStore:
    """BridgeStore-compatible backend using one transaction per public operation."""

    engine = "postgresql"

    def __init__(
        self,
        database_url: str,
        *,
        migration_database_url: str = "",
        migration_mode: Optional[str] = None,
    ) -> None:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("AgentTeams bridge PostgreSQL URL must use postgresql://")
        if migration_database_url and not migration_database_url.startswith(
            ("postgresql://", "postgres://")
        ):
            raise ValueError("AgentTeams bridge migration URL must use postgresql://")
        self.database_url = database_url
        self.migration_database_url = migration_database_url or database_url
        self.migration_mode = (
            migration_mode or os.getenv("EGO_AGENTTEAMS_MIGRATION_MODE", "apply") or "apply"
        ).strip()
        if self.migration_mode not in {"apply", "verify"}:
            raise ValueError("EGO_AGENTTEAMS_MIGRATION_MODE must be apply or verify")
        self.initialize()

    def _connect(self, database_url: Optional[str] = None) -> Connection[Dict[str, Any]]:
        connection: Connection[Dict[str, Any]] = psycopg.connect(
            database_url or self.database_url,
            autocommit=True,
            row_factory=dict_row,
            application_name="egoagentos-agentteams-bridge",
        )
        try:
            connection.execute("SET TIME ZONE 'UTC'")
        except Exception:
            connection.close()
            raise
        return connection

    @contextmanager
    def _transaction(
        self, database_url: Optional[str] = None
    ) -> Iterator[Connection[Dict[str, Any]]]:
        connection = self._connect(database_url)
        try:
            connection.execute("BEGIN")
            try:
                yield connection
            except Exception:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
        finally:
            connection.close()

    @staticmethod
    def _json(value: Any) -> Any:
        return json.loads(value) if isinstance(value, str) else value

    @staticmethod
    def _iso(value: Any) -> str:
        if isinstance(value, datetime):
            return _utc_iso(value)
        return str(value)

    @classmethod
    def _row_to_run(cls, row: Mapping[str, Any]) -> BridgeRun:
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
                "task_graph": cls._json(row["task_graph"]),
                "checkpoint": cls._json(row["checkpoint"]),
                "ack_timeout_seconds": row["ack_timeout_seconds"],
                "execution_timeout_seconds": row["execution_timeout_seconds"],
                "max_reassignments": row["max_reassignments"],
                "version": row["version"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
        )

    def initialize(self) -> None:
        migration_root = resources.files("apps.agentteams_bridge").joinpath("migrations/postgres")
        migration_files = sorted(
            (entry for entry in migration_root.iterdir() if entry.name.endswith(".sql")),
            key=lambda entry: entry.name,
        )
        packaged = {
            migration.name: hashlib.sha256(
                migration.read_text(encoding="utf-8").encode("utf-8")
            ).hexdigest()
            for migration in migration_files
        }
        if self.migration_mode == "verify":
            with self._transaction(self.migration_database_url) as connection:
                rows = connection.execute(
                    "SELECT version, sha256 FROM bridge_schema_migrations ORDER BY version"
                ).fetchall()
            observed = {str(row["version"]): str(row["sha256"]) for row in rows}
            if observed != packaged:
                raise RuntimeError(
                    "AgentTeams bridge migrations do not exactly match packaged SQL in verify mode"
                )
            return
        with self._transaction(self.migration_database_url) as connection:
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('egoagentos:bridge:migrations', 0))"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS bridge_schema_migrations (
                    version TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            rows = connection.execute(
                "SELECT version, sha256 FROM bridge_schema_migrations"
            ).fetchall()
            applied = {str(row["version"]): str(row["sha256"]) for row in rows}
            for migration in migration_files:
                migration_sql = migration.read_text(encoding="utf-8")
                digest = packaged[migration.name]
                if migration.name in applied:
                    if applied[migration.name] != digest:
                        raise RuntimeError(
                            "applied AgentTeams bridge migration checksum differs: %s"
                            % migration.name
                        )
                    continue
                connection.execute(migration_sql)
                connection.execute(
                    "INSERT INTO bridge_schema_migrations(version, sha256) VALUES (%s, %s)",
                    (migration.name, digest),
                )

    def create_run(self, run: BridgeRun) -> BridgeRun:
        try:
            with self._transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO bridge_runs (
                        id, ego_task_id, agentteams_project_id, team, trace_id,
                        correlation_id, context_version, state, mode, objective,
                        task_graph, checkpoint, ack_timeout_seconds,
                        execution_timeout_seconds, max_reassignments, version,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s
                    )
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
                        Jsonb([task.model_dump(mode="json") for task in run.task_graph]),
                        Jsonb(run.checkpoint),
                        run.ack_timeout_seconds,
                        run.execution_timeout_seconds,
                        run.max_reassignments,
                        run.version,
                        run.created_at,
                        run.updated_at,
                    ),
                )
        except UniqueViolation as error:
            duplicate_id = error.diag.constraint_name == "bridge_runs_pkey"
            raise BridgeError(
                "run_conflict",
                (
                    "A bridge run with this ID already exists"
                    if duplicate_id
                    else "A bridge run already owns this AgentTeams project"
                ),
                details=(
                    {"run_id": run.id}
                    if duplicate_id
                    else {"project_id": run.agentteams_project_id}
                ),
            ) from error
        return run

    def get_run(self, run_id: str) -> BridgeRun:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM bridge_runs WHERE id=%s", (run_id,)).fetchone()
        finally:
            connection.close()
        if row is None:
            raise BridgeError(
                "run_not_found",
                "Bridge run was not found",
                status_code=404,
                details={"id": run_id},
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
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT *, clock_timestamp() AS lease_now FROM bridge_runs WHERE id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if row is None:
                raise BridgeError(
                    "run_not_found",
                    "Bridge run was not found",
                    status_code=404,
                    details={"id": run_id},
                )
            checkpoint = self._json(row["checkpoint"])
            acquired_at = row["lease_now"]
            _raise_if_lease_held(checkpoint, run_id=run_id, acquired_at=acquired_at)
            checkpoint[OPERATION_LEASE_KEY] = _lease_payload(
                operation, owner_id, acquired_at, timeout_seconds
            )
            claimed = connection.execute(
                "UPDATE bridge_runs SET checkpoint=%s WHERE id=%s RETURNING *",
                (Jsonb(checkpoint), run_id),
            ).fetchone()
            if claimed is None:
                raise BridgeError(
                    "operation_claim_failed",
                    "Bridge operation lease could not be persisted",
                    retryable=True,
                    details={"run_id": run_id},
                )
        return self._row_to_run(claimed)

    def release_operation(self, run_id: str, owner_id: str) -> None:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT checkpoint FROM bridge_runs WHERE id=%s FOR UPDATE", (run_id,)
            ).fetchone()
            if row is None:
                return
            checkpoint = self._json(row["checkpoint"])
            lease = _operation_lease(checkpoint)
            if lease is None or lease.get("owner_id") != owner_id:
                return
            checkpoint.pop(OPERATION_LEASE_KEY, None)
            connection.execute(
                "UPDATE bridge_runs SET checkpoint=%s WHERE id=%s",
                (Jsonb(checkpoint), run_id),
            )

    def renew_operation(
        self,
        run_id: str,
        owner_id: str,
        *,
        timeout_seconds: int,
    ) -> BridgeRun:
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT *, clock_timestamp() AS lease_now FROM bridge_runs WHERE id=%s FOR UPDATE",
                (run_id,),
            ).fetchone()
            if row is None:
                raise BridgeError(
                    "run_not_found",
                    "Bridge run was not found",
                    status_code=404,
                    details={"id": run_id},
                )
            checkpoint = self._json(row["checkpoint"])
            lease = _operation_lease(checkpoint)
            _assert_update_lease(
                checkpoint,
                checkpoint,
                run_id=run_id,
                lease_owner=owner_id,
                checked_at=row["lease_now"],
            )
            assert lease is not None
            checkpoint[OPERATION_LEASE_KEY] = _lease_payload(
                str(lease["operation"]),
                owner_id,
                row["lease_now"],
                timeout_seconds,
            )
            renewed = connection.execute(
                "UPDATE bridge_runs SET checkpoint=%s WHERE id=%s RETURNING *",
                (Jsonb(checkpoint), run_id),
            ).fetchone()
            if renewed is None:
                raise BridgeError(
                    "operation_renew_failed",
                    "Bridge operation lease could not be renewed",
                    retryable=True,
                    details={"run_id": run_id},
                )
        return self._row_to_run(renewed)

    def update_run(
        self,
        run: BridgeRun,
        *,
        expected_version: int,
        lease_owner: Optional[str] = None,
    ) -> BridgeRun:
        updated = run.model_copy(update={"version": expected_version + 1, "updated_at": utc_now()})
        with self._transaction() as connection:
            current = connection.execute(
                """
                SELECT version, checkpoint, clock_timestamp() AS lease_now
                FROM bridge_runs WHERE id=%s FOR UPDATE
                """,
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
                self._json(current["checkpoint"]),
                updated.checkpoint,
                run_id=run.id,
                lease_owner=lease_owner,
                checked_at=current["lease_now"],
            )
            cursor = connection.execute(
                """
                UPDATE bridge_runs SET
                    state=%s, task_graph=%s, checkpoint=%s, version=%s, updated_at=%s
                WHERE id=%s AND version=%s
                """,
                (
                    updated.state.value,
                    Jsonb([task.model_dump(mode="json") for task in updated.task_graph]),
                    Jsonb(updated.checkpoint),
                    updated.version,
                    updated.updated_at,
                    updated.id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                raise BridgeError(
                    "run_version_conflict",
                    "Bridge run was concurrently modified; reload before retrying",
                    retryable=True,
                    details={"run_id": run.id, "expected_version": expected_version},
                )
        return updated

    @staticmethod
    def _advisory_lock(connection: Connection[Dict[str, Any]], *, stream: str, run_id: str) -> None:
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            ("egoagentos:bridge:%s:%s" % (stream, run_id),),
        )

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
        with self._transaction() as connection:
            run_row = connection.execute(
                """
                SELECT checkpoint, clock_timestamp() AS lease_now
                FROM bridge_runs WHERE id=%s FOR UPDATE
                """,
                (run_id,),
            ).fetchone()
            if run_row is None:
                raise BridgeError(
                    "run_not_found",
                    "Bridge run was not found",
                    status_code=404,
                    details={"id": run_id},
                )
            checkpoint = self._json(run_row["checkpoint"])
            _assert_update_lease(
                checkpoint,
                checkpoint,
                run_id=run_id,
                lease_owner=lease_owner,
                checked_at=run_row["lease_now"],
            )
            self._advisory_lock(connection, stream="event", run_id=run_id)
            row = connection.execute(
                """
                SELECT event_hash FROM bridge_events
                 WHERE run_id=%s ORDER BY sequence DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            previous_hash = str(row["event_hash"]) if row is not None else ZERO_HASH
            hash_payload = {
                "event_id": event_id,
                "run_id": run_id,
                "kind": envelope.kind.value,
                "envelope": envelope_payload,
                "previous_hash": previous_hash,
                "created_at": created_at,
            }
            event_hash = hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()
            inserted = connection.execute(
                """
                INSERT INTO bridge_events(
                    event_id, run_id, kind, envelope, previous_hash, event_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING sequence
                """,
                (
                    event_id,
                    run_id,
                    envelope.kind.value,
                    Jsonb(envelope_payload),
                    previous_hash,
                    event_hash,
                    created_at,
                ),
            ).fetchone()
            if inserted is None:
                raise BridgeError(
                    "event_sequence_missing",
                    "PostgreSQL did not return a bridge event sequence",
                )
            sequence = int(inserted["sequence"])
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
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM bridge_events WHERE run_id=%s ORDER BY sequence", (run_id,)
            ).fetchall()
        finally:
            connection.close()
        items: List[Dict[str, Any]] = []
        expected_previous = ZERO_HASH
        chain_valid = True
        for row in rows:
            envelope = self._json(row["envelope"])
            created_at = self._iso(row["created_at"])
            hash_payload = {
                "event_id": row["event_id"],
                "run_id": row["run_id"],
                "kind": row["kind"],
                "envelope": envelope,
                "previous_hash": row["previous_hash"],
                "created_at": created_at,
            }
            expected_hash = hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()
            if row["previous_hash"] != expected_previous or row["event_hash"] != expected_hash:
                chain_valid = False
            expected_previous = str(row["event_hash"])
            items.append(
                {
                    "sequence": int(row["sequence"]),
                    "event_id": row["event_id"],
                    "kind": row["kind"],
                    "envelope": envelope,
                    "previous_hash": row["previous_hash"],
                    "event_hash": row["event_hash"],
                    "created_at": created_at,
                }
            )
        return {"items": items, "total": len(items), "chain_valid": chain_valid}

    @classmethod
    def _receipt_row(
        cls, row: Mapping[str, Any], *, idempotent_replay: bool = False
    ) -> Dict[str, Any]:
        return {
            "sequence": int(row["sequence"]),
            "receipt_id": row["receipt_id"],
            "run_id": row["run_id"],
            "receipt_key": row["receipt_key"],
            "source": row["source"],
            "kind": row["kind"],
            "payload": cls._json(row["payload"]),
            "payload_sha256": row["payload_sha256"],
            "previous_hash": row["previous_hash"],
            "receipt_hash": row["receipt_hash"],
            "created_at": cls._iso(row["created_at"]),
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
        payload_json = canonical_json(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            run_row = connection.execute(
                """
                SELECT checkpoint, clock_timestamp() AS lease_now
                FROM bridge_runs WHERE id=%s FOR UPDATE
                """,
                (run_id,),
            ).fetchone()
            if run_row is None:
                raise BridgeError(
                    "run_not_found",
                    "Bridge run was not found",
                    status_code=404,
                    details={"id": run_id},
                )
            checkpoint = self._json(run_row["checkpoint"])
            _assert_update_lease(
                checkpoint,
                checkpoint,
                run_id=run_id,
                lease_owner=lease_owner,
                checked_at=run_row["lease_now"],
            )
            self._advisory_lock(connection, stream="receipt", run_id=run_id)
            existing = connection.execute(
                """
                SELECT * FROM bridge_receipts WHERE run_id=%s AND receipt_key=%s
                """,
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
                return self._receipt_row(existing, idempotent_replay=True)
            previous = connection.execute(
                """
                SELECT receipt_hash FROM bridge_receipts
                 WHERE run_id=%s ORDER BY sequence DESC LIMIT 1
                """,
                (run_id,),
            ).fetchone()
            previous_hash = str(previous["receipt_hash"]) if previous is not None else ZERO_HASH
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
            receipt_hash = hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()
            row = connection.execute(
                """
                INSERT INTO bridge_receipts(
                    receipt_id, run_id, receipt_key, source, kind, payload,
                    payload_sha256, previous_hash, receipt_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (
                    receipt_id,
                    run_id,
                    receipt_key,
                    source,
                    kind,
                    Jsonb(payload),
                    payload_sha256,
                    previous_hash,
                    receipt_hash,
                    created_at,
                ),
            ).fetchone()
            if row is None:
                raise BridgeError("receipt_missing", "Archived receipt could not be reloaded")
            return self._receipt_row(row)

    def receipts(self, run_id: str) -> Dict[str, Any]:
        self.get_run(run_id)
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM bridge_receipts WHERE run_id=%s ORDER BY sequence", (run_id,)
            ).fetchall()
        finally:
            connection.close()
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
            expected_previous = str(item["receipt_hash"])
            items.append(item)
        return {"items": items, "total": len(items), "chain_valid": chain_valid}

    @classmethod
    def _campaign_row(cls, row: Mapping[str, Any]) -> Dict[str, Any]:
        return {
            "run_id": row["id"],
            "project_id": row["agentteams_project_id"],
            "binding": cls._json(row["campaign_binding"]),
            "binding_sha256": row["campaign_binding_sha256"],
        }

    def bind_campaign(self, run_id: str, binding: CampaignBinding) -> Dict[str, Any]:
        payload = binding.model_dump(mode="json")
        payload_json = canonical_json(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        with self._transaction() as connection:
            self._advisory_lock(connection, stream="extension", run_id=run_id)
            row = connection.execute(
                "SELECT * FROM bridge_runs WHERE id=%s", (run_id,)
            ).fetchone()
            if row is None:
                raise BridgeError("run_not_found", "Bridge run was not found", status_code=404)
            if row["campaign_binding"] is not None:
                if canonical_json(self._json(row["campaign_binding"])) != payload_json:
                    raise BridgeError(
                        "campaign_binding_conflict",
                        "Bridge run already has a different immutable campaign binding",
                    )
                return self._campaign_row(row)
            stored = connection.execute(
                """
                UPDATE bridge_runs SET
                    tenant_id=NULLIF(current_setting('egoagentos.tenant_id', true), ''),
                    campaign_binding=%s, campaign_binding_sha256=%s,
                    campaign_id=%s, configuration_id=%s, execution_phase_owner=%s,
                    problem_id=%s, campaign_turn=%s, campaign_generation=%s,
                    manifest_sha256=%s, post_selection_extension_sha256=%s,
                    policy_sha256=%s, requirement_ledger_sha256=%s,
                    workspace_checkpoint_sha256=%s, memory_watermark=%s
                WHERE id=%s AND campaign_binding IS NULL
                RETURNING *
                """,
                (
                    Jsonb(payload),
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
            ).fetchone()
            if stored is None:
                raise BridgeError("campaign_binding_conflict", "Campaign binding raced")
            return self._campaign_row(stored)

    def campaign_binding(
        self,
        run_id: str,
        *,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM bridge_runs
                WHERE id=%s AND agentteams_project_id=%s
                  AND configuration_id IS NOT DISTINCT FROM %s
                  AND campaign_binding IS NOT NULL
                """,
                (run_id, project_id, configuration_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise BridgeError(
                "campaign_binding_not_found",
                "No campaign binding matches this run, project, and configuration",
                status_code=404,
            )
        return self._campaign_row(row)

    @classmethod
    def _extension_row(
        cls, row: Mapping[str, Any], *, idempotent_replay: bool = False
    ) -> Dict[str, Any]:
        raw = bytes(row["canonical_payload"])
        return {
            "sequence": int(row["sequence"]),
            "event_id": row["event_id"],
            "run_id": row["run_id"],
            "event_type": row["event_type"],
            "idempotency_key": row["idempotency_key"],
            "event": json.loads(raw.decode("utf-8")),
            "canonical_payload": raw,
            "payload_sha256": row["payload_sha256"],
            "memory_watermark": int(row["memory_watermark"]),
            "previous_hash": row["previous_hash"],
            "event_hash": row["event_hash"],
            "created_at": cls._iso(row["created_at"]),
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
        payload = BridgeStore._typed_extension_payload(event_type, event)
        canonical_payload = canonical_json(payload).encode("utf-8")
        with self._transaction() as connection:
            return self._append_extension_bytes_locked(
                connection,
                run_id,
                event_type=event_type,
                canonical_payload=canonical_payload,
                idempotency_key=idempotency_key,
                memory_watermark=memory_watermark,
            )

    def _append_extension_bytes_locked(
        self,
        connection: Connection[Dict[str, Any]],
        run_id: str,
        *,
        event_type: str,
        canonical_payload: bytes,
        idempotency_key: str,
        memory_watermark: int,
    ) -> Dict[str, Any]:
        self._advisory_lock(connection, stream="extension", run_id=run_id)
        run = connection.execute(
            "SELECT * FROM bridge_runs WHERE id=%s", (run_id,)
        ).fetchone()
        if run is None:
            raise BridgeError("run_not_found", "Bridge run was not found", status_code=404)
        if run["campaign_binding"] is None:
            raise BridgeError("campaign_binding_required", "Campaign binding is required")
        if memory_watermark > int(run["memory_watermark"]):
            raise BridgeError(
                "future_memory_watermark", "Extension event cites a future watermark"
            )
        existing = connection.execute(
            """
            SELECT * FROM bridge_extension_events
            WHERE run_id=%s AND idempotency_key=%s
            """,
            (run_id, idempotency_key),
        ).fetchone()
        if existing is not None:
            if (
                bytes(existing["canonical_payload"]) != canonical_payload
                or existing["event_type"] != event_type
                or int(existing["memory_watermark"]) != memory_watermark
            ):
                raise BridgeError(
                    "idempotency_conflict",
                    "Idempotency key was replayed with different canonical bytes",
                )
            return self._extension_row(existing, idempotent_replay=True)
        payload = json.loads(canonical_payload.decode("utf-8"))
        self._validate_extension_admission_locked(
            connection,
            run,
            event_type=event_type,
            payload=payload,
            memory_watermark=memory_watermark,
        )
        previous = connection.execute(
            """
            SELECT sequence, event_hash FROM bridge_extension_events
            WHERE run_id=%s ORDER BY sequence DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        sequence = int(previous["sequence"]) + 1 if previous else 1
        previous_hash = str(previous["event_hash"]) if previous else ZERO_HASH
        event_id = "xevt_%s" % uuid.uuid4().hex
        created_at = _utc_iso(utc_now())
        payload_sha256 = hashlib.sha256(canonical_payload).hexdigest()
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
        row = connection.execute(
            """
            INSERT INTO bridge_extension_events(
                run_id, sequence, event_id, event_type, idempotency_key,
                canonical_payload, payload_sha256, memory_watermark,
                previous_hash, event_hash, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING *
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
        ).fetchone()
        if row is None:
            raise BridgeError("extension_event_missing", "Extension event was not stored")
        return self._extension_row(row)

    @staticmethod
    def _has_exact_extension_event_locked(
        connection: Connection[Dict[str, Any]],
        run_id: str,
        *,
        event_type: str,
        payload: Dict[str, Any],
    ) -> bool:
        row = connection.execute(
            """
            SELECT 1 FROM bridge_extension_events
            WHERE run_id=%s AND event_type=%s AND canonical_payload=%s
            """,
            (run_id, event_type, canonical_json(payload).encode("utf-8")),
        ).fetchone()
        return row is not None

    @staticmethod
    def _require_task_lease_locked(
        connection: Connection[Dict[str, Any]], run_id: str, task_id: str
    ) -> None:
        if connection.execute(
            "SELECT 1 FROM bridge_task_leases WHERE run_id=%s AND task_id=%s",
            (run_id, task_id),
        ).fetchone() is None:
            raise BridgeError("task_lease_required", "Exact admitted task lease is required")

    def _validate_extension_admission_locked(
        self,
        connection: Connection[Dict[str, Any]],
        run: Mapping[str, Any],
        *,
        event_type: str,
        payload: Dict[str, Any],
        memory_watermark: int,
    ) -> None:
        run_id = str(run["id"])
        binding = self._json(run["campaign_binding"])
        if event_type == "CANONICAL_EFFECT":
            if (
                payload["project_id"] != run["agentteams_project_id"]
                or payload["workspace_checkpoint_sha256"]
                != binding["workspace_checkpoint_sha256"]
                or payload["policy_sha256"] != binding["policy_sha256"]
            ):
                raise BridgeError("effect_binding_mismatch", "Effect binding mismatch")
            self._require_task_lease_locked(connection, run_id, payload["task_id"])
        elif event_type == "GUARDIAN_DECISION":
            system = payload["system_assessment"]
            if system["risk_level"] != "HIGH" or not self._has_exact_extension_event_locked(
                connection,
                run_id,
                event_type="SYSTEM_RISK_ASSESSMENT",
                payload=system,
            ):
                raise BridgeError(
                    "guardian_order_invalid", "Matching admitted system-HIGH is required"
                )
        elif event_type == "SAFETY_DECISION":
            if not self._has_exact_extension_event_locked(
                connection,
                run_id,
                event_type="CANONICAL_EFFECT",
                payload=payload["effect"],
            ) or not self._has_exact_extension_event_locked(
                connection,
                run_id,
                event_type="GUARDIAN_DECISION",
                payload=payload["guardian_decision"],
            ):
                raise BridgeError(
                    "safety_binding_unadmitted", "Exact effect and Guardian evidence required"
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
                raise BridgeError("attention_binding_mismatch", "Attention binding mismatch")
            self._require_task_lease_locked(connection, run_id, payload["task_id"])
        elif event_type == "USER_STATUS_PROJECTION":
            if payload["project_id"] != run["agentteams_project_id"]:
                raise BridgeError("projection_binding_mismatch", "Projection project mismatch")
            self._require_task_lease_locked(connection, run_id, payload["task_id"])
            source_ids = payload["source_event_ids"]
            if source_ids:
                admitted = connection.execute(
                    """
                    SELECT count(*) AS total FROM bridge_extension_events
                    WHERE run_id=%s AND event_id = ANY(%s)
                    """,
                    (run_id, list(source_ids)),
                ).fetchone()
                if admitted is None or int(admitted["total"]) != len(source_ids):
                    raise BridgeError(
                        "projection_source_unadmitted", "Projection source is not admitted"
                    )
            guardian = payload.get("guardian_decision")
            safety = payload.get("safety_decision")
            if guardian is not None and not self._has_exact_extension_event_locked(
                connection,
                run_id,
                event_type="GUARDIAN_DECISION",
                payload=guardian,
            ):
                raise BridgeError(
                    "projection_source_unadmitted", "Projection Guardian is not admitted"
                )
            if safety is not None and not self._has_exact_extension_event_locked(
                connection,
                run_id,
                event_type="SAFETY_DECISION",
                payload=safety,
            ):
                raise BridgeError(
                    "projection_source_unadmitted", "Projection Safety is not admitted"
                )

    def extension_events(
        self,
        run_id: str,
        *,
        project_id: str,
        configuration_id: Optional[str],
    ) -> Dict[str, Any]:
        self.campaign_binding(
            run_id, project_id=project_id, configuration_id=configuration_id
        )
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT * FROM bridge_extension_events WHERE run_id=%s ORDER BY sequence",
                (run_id,),
            ).fetchall()
        finally:
            connection.close()
        items: List[Dict[str, Any]] = []
        expected_previous = ZERO_HASH
        chain_valid = True
        for row in rows:
            item = self._extension_row(row)
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
            expected_hash = hashlib.sha256(canonical_json(hash_payload).encode("utf-8")).hexdigest()
            if (
                item["payload_sha256"]
                != hashlib.sha256(item["canonical_payload"]).hexdigest()
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
            run_id, project_id=project_id, configuration_id=configuration_id
        )
        return bool(replay["chain_valid"] and replay["root_hash"] == expected_root_hash)

    @classmethod
    def _lease_row(
        cls, row: Mapping[str, Any], *, idempotent_replay: bool = False
    ) -> Dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "task_id": row["task_id"],
            "event_sequence": int(row["event_sequence"]),
            "idempotency_key": row["idempotency_key"],
            "canonical_signed_payload": bytes(row["canonical_signed_payload"]),
            "payload_sha256": row["payload_sha256"],
            "signature_base64": row["signature_base64"],
            "key_id": row["key_id"],
            "issuer_id": row["issuer_id"],
            "previous_stream_digest": row["previous_stream_digest"],
            "stream_digest": row["stream_digest"],
            "created_at": cls._iso(row["created_at"]),
            "idempotent_replay": idempotent_replay,
        }

    @classmethod
    def _evaluator_row(
        cls, row: Mapping[str, Any], *, idempotent_replay: bool = False
    ) -> Dict[str, Any]:
        return {
            "run_id": row["run_id"],
            "binding_id": row["binding_id"],
            "task_id": row["task_id"],
            "event_sequence": int(row["event_sequence"]),
            "idempotency_key": row["idempotency_key"],
            "canonical_signed_payload": bytes(row["canonical_signed_payload"]),
            "payload_sha256": row["payload_sha256"],
            "signature_base64": row["signature_base64"],
            "key_id": row["key_id"],
            "issuer_id": row["issuer_id"],
            "previous_stream_digest": row["previous_stream_digest"],
            "stream_digest": row["stream_digest"],
            "created_at": cls._iso(row["created_at"]),
            "idempotent_replay": idempotent_replay,
        }

    def store_signed_task_lease(
        self,
        run_id: str,
        *,
        already_verified_signed_payload: bytes,
        idempotency_key: str,
    ) -> Dict[str, Any]:
        payload = BridgeStore._require_canonical_object(already_verified_signed_payload)
        try:
            lease = SignedTaskLease.model_validate(payload)
        except (TypeError, ValueError) as error:
            raise BridgeError(
                "verified_task_lease_invalid", "Payload is not a signed task lease"
            ) from error
        with self._transaction() as connection:
            self._advisory_lock(connection, stream="extension", run_id=run_id)
            run = connection.execute(
                "SELECT * FROM bridge_runs WHERE id=%s", (run_id,)
            ).fetchone()
            if run is None:
                raise BridgeError("run_not_found", "Bridge run was not found", status_code=404)
            if run["campaign_binding"] is None:
                raise BridgeError("campaign_binding_required", "Campaign binding required")
            binding_row = dict(run)
            binding_row["campaign_binding_json"] = canonical_json(
                self._json(run["campaign_binding"])
            )
            BridgeStore._assert_lease_binding(binding_row, lease)  # type: ignore[arg-type]
            event = self._append_extension_bytes_locked(
                connection,
                run_id,
                event_type="VERIFIED_TASK_LEASE_ADMISSION",
                canonical_payload=already_verified_signed_payload,
                idempotency_key=idempotency_key,
                memory_watermark=lease.core.memory_watermark,
            )
            existing = connection.execute(
                "SELECT * FROM bridge_task_leases WHERE run_id=%s AND task_id=%s",
                (run_id, lease.core.task_id),
            ).fetchone()
            if existing is not None:
                if int(existing["event_sequence"]) != event["sequence"]:
                    raise BridgeError("task_lease_conflict", "Task has another lease")
                return self._lease_row(existing, idempotent_replay=True)
            row = connection.execute(
                """
                INSERT INTO bridge_task_leases(
                    run_id, task_id, event_sequence, idempotency_key,
                    canonical_signed_payload, payload_sha256, signature_base64,
                    key_id, issuer_id, previous_stream_digest, stream_digest, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
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
            ).fetchone()
            if row is None:
                raise BridgeError("task_lease_missing", "Task lease was not stored")
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
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM bridge_task_leases WHERE run_id=%s AND task_id=%s",
                (run_id, task_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise BridgeError("task_lease_not_found", "Task lease was not found", status_code=404)
        return self._lease_row(row)

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
                "verified_evaluator_binding_invalid", "Public metadata must be non-empty"
            )
        payload = BridgeStore._require_canonical_object(already_verified_signed_payload)
        BridgeStore._reject_private_verification_material(payload)
        if payload.get("task_id") != task_id:
            raise BridgeError(
                "verified_evaluator_binding_invalid", "Evaluator task binding mismatch"
            )
        with self._transaction() as connection:
            self._advisory_lock(connection, stream="extension", run_id=run_id)
            run = connection.execute(
                "SELECT * FROM bridge_runs WHERE id=%s", (run_id,)
            ).fetchone()
            if run is None:
                raise BridgeError("run_not_found", "Bridge run was not found", status_code=404)
            if connection.execute(
                "SELECT 1 FROM bridge_task_leases WHERE run_id=%s AND task_id=%s",
                (run_id, task_id),
            ).fetchone() is None:
                raise BridgeError("task_lease_required", "Exact task lease is required")
            event = self._append_extension_bytes_locked(
                connection,
                run_id,
                event_type="VERIFIED_EVALUATOR_ADMISSION",
                canonical_payload=already_verified_signed_payload,
                idempotency_key=idempotency_key,
                memory_watermark=int(run["memory_watermark"]),
            )
            existing = connection.execute(
                """
                SELECT * FROM bridge_evaluator_bindings
                WHERE run_id=%s AND binding_id=%s
                """,
                (run_id, binding_id),
            ).fetchone()
            if existing is not None:
                if int(existing["event_sequence"]) != event["sequence"]:
                    raise BridgeError("evaluator_binding_conflict", "Binding ID conflict")
                return self._evaluator_row(existing, idempotent_replay=True)
            row = connection.execute(
                """
                INSERT INTO bridge_evaluator_bindings(
                    run_id, binding_id, task_id, event_sequence, idempotency_key,
                    canonical_signed_payload, payload_sha256, signature_base64,
                    key_id, issuer_id, previous_stream_digest, stream_digest, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING *
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
            ).fetchone()
            if row is None:
                raise BridgeError("evaluator_binding_missing", "Binding was not stored")
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
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM bridge_evaluator_bindings
                WHERE run_id=%s AND binding_id=%s
                """,
                (run_id, binding_id),
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise BridgeError(
                "evaluator_binding_not_found", "Evaluator binding not found", status_code=404
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
        connection = self._connect()
        try:
            leases = connection.execute(
                "SELECT * FROM bridge_task_leases WHERE run_id=%s ORDER BY event_sequence",
                (run_id,),
            ).fetchall()
            evaluators = connection.execute(
                """
                SELECT * FROM bridge_evaluator_bindings
                WHERE run_id=%s ORDER BY event_sequence
                """,
                (run_id,),
            ).fetchall()
        finally:
            connection.close()
        items = events["items"]
        projections = [item for item in items if item["event_type"] == "USER_STATUS_PROJECTION"]
        return {
            "campaign_binding": campaign,
            "task_leases": [self._lease_row(row) for row in leases],
            "evaluator_bindings": [self._evaluator_row(row) for row in evaluators],
            "guardian_decisions": [
                item for item in items if item["event_type"] == "GUARDIAN_DECISION"
            ],
            "safety_decisions": [
                item for item in items if item["event_type"] == "SAFETY_DECISION"
            ],
            "projection": projections[-1] if projections else None,
            "events": events,
        }

    def active_runs(self) -> List[BridgeRun]:
        terminal = (RunState.BLOCKED.value, RunState.COMPLETED.value)
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM bridge_runs
                 WHERE state NOT IN (%s, %s) ORDER BY created_at
                """,
                terminal,
            ).fetchall()
        finally:
            connection.close()
        return [self._row_to_run(row) for row in rows]

    def ping(self) -> bool:
        connection = self._connect()
        try:
            row: Optional[Mapping[str, Any]] = connection.execute("SELECT 1 AS ready").fetchone()
            return row is not None and row["ready"] == 1
        finally:
            connection.close()
