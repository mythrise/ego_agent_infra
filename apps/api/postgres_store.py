"""PostgreSQL/PolarDB-PG compatible persistence for the ResearchOps control plane."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from importlib import resources
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple
from urllib.parse import urlsplit

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from benchmarks.secure_memory.canonical import (
    canonical_bytes,
    canonical_sha256 as secure_memory_canonical_sha256,
    parse_json_bytes,
    validate_sha256_digest,
)

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
    LegacyMemoryView,
    LifecycleTransition,
    MemoryState,
    RevocationRecord,
    SupersessionRecord,
    TrustedFact,
)


STAGE_EVENT_CHANNEL = "ego_stage_events"
TRUSTED_MEMORY_EVENT_CHANNEL = "ego_trusted_memory_events"
ZERO_HASH = "0" * 64


def _json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("persisted JSON document must be an object")
        return parsed
    if isinstance(value, dict):
        return value
    raise ValueError("persisted JSON document must be an object")


def _safe_location(database_url: str) -> str:
    parsed = urlsplit(database_url)
    host = parsed.hostname or "localhost"
    port = ":%s" % parsed.port if parsed.port else ""
    database = parsed.path.lstrip("/") or "postgres"
    return "%s%s/%s" % (host, port, database)


class PostgresStore:
    """Synchronous psycopg implementation of the complete ResearchStore contract.

    PolarDB for PostgreSQL uses PostgreSQL-compatible wire and SQL interfaces. This
    implementation is deliberately verified against local PostgreSQL only; cloud
    compatibility does not imply that a PolarDB instance or PITR workflow was exercised.
    """

    engine = "postgresql"
    audit_guarantee = "trigger_immutable_predecessor_guarded_hash_chain"

    def __init__(
        self,
        database_url: str,
        tenant_id: Optional[str] = None,
        migration_mode: Optional[str] = None,
    ) -> None:
        if not database_url.startswith(("postgresql://", "postgres://")):
            raise ValueError("PostgresStore requires a postgresql:// or postgres:// URL")
        self.database_url = database_url
        self.db_path = _safe_location(database_url)
        self.location = self.db_path
        self.tenant_id = tenant_id or os.getenv("EGO_TENANT_ID", "local")
        if not self.tenant_id or len(self.tenant_id) > 128:
            raise ValueError("EGO_TENANT_ID must contain between 1 and 128 characters")
        self._lock = threading.RLock()
        self._transaction_local = threading.local()
        self.migration_mode = (
            migration_mode or os.getenv("EGO_DATABASE_MIGRATION_MODE", "apply").strip()
        )
        if self.migration_mode not in {"apply", "verify"}:
            raise ValueError("EGO_DATABASE_MIGRATION_MODE must be apply or verify")
        self.initialize()

    def _new_connection(self, *, autocommit: bool = False) -> Connection[Dict[str, Any]]:
        connection: Connection[Dict[str, Any]] = psycopg.connect(
            self.database_url,
            autocommit=autocommit,
            row_factory=dict_row,
            application_name="egoagentos-researchops",
        )
        connection.execute(
            "SELECT set_config('egoagentos.tenant_id', %s, false)", (self.tenant_id,)
        )
        connection.execute("SET TIME ZONE 'UTC'")
        if not autocommit:
            connection.commit()
        return connection

    def _active_transaction(self) -> Optional[Connection[Dict[str, Any]]]:
        active: Optional[Connection[Dict[str, Any]]] = getattr(
            self._transaction_local, "connection", None
        )
        return active

    def _connect(self) -> Connection[Dict[str, Any]]:
        return self._active_transaction() or self._new_connection()

    def _close(self, connection: Connection[Dict[str, Any]]) -> None:
        if connection is not self._active_transaction():
            connection.close()

    def _commit(self, connection: Connection[Dict[str, Any]]) -> None:
        if connection is not self._active_transaction():
            connection.commit()

    def _rollback(self, connection: Connection[Dict[str, Any]]) -> None:
        if connection is not self._active_transaction():
            connection.rollback()

    @contextmanager
    def mutation_lock(self) -> Iterator[None]:
        """Compatibility scope; PostgreSQL uses row/advisory transaction locks."""

        yield

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Commit a logical service mutation atomically, including nested scopes."""

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

        connection = self._new_connection()
        try:
            connection.execute("BEGIN")
            connection.execute(
                "SELECT set_config('egoagentos.tenant_id', %s, true)", (self.tenant_id,)
            )
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
            connection.close()

    def initialize(self) -> None:
        migration_root = resources.files("apps.api").joinpath("migrations/postgres")
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
            connection = self._new_connection()
            try:
                rows = connection.execute(
                    "SELECT version, sha256 FROM schema_migrations ORDER BY version"
                ).fetchall()
            finally:
                connection.close()
            observed = {str(row["version"]): str(row["sha256"]) for row in rows}
            if observed != packaged:
                raise RuntimeError(
                    "database migrations do not exactly match packaged SQL in verify mode"
                )
            return
        connection = self._new_connection()
        try:
            connection.execute("BEGIN")
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('egoagentos:migrations', 0))"
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version TEXT PRIMARY KEY,
                    sha256 TEXT NOT NULL CHECK (sha256 ~ '^[0-9a-f]{64}$'),
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            rows = connection.execute("SELECT version, sha256 FROM schema_migrations").fetchall()
            applied = {str(row["version"]): str(row["sha256"]) for row in rows}
            for migration in migration_files:
                migration_sql = migration.read_text(encoding="utf-8")
                migration_digest = packaged[migration.name]
                if migration.name in applied:
                    if applied[migration.name] != migration_digest:
                        raise RuntimeError(
                            "applied migration checksum differs from packaged SQL: %s"
                            % migration.name
                        )
                    continue
                connection.execute(migration_sql)
                connection.execute(
                    "INSERT INTO schema_migrations(version, sha256) VALUES (%s, %s)",
                    (migration.name, migration_digest),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def ping(self) -> bool:
        connection = self._connect()
        try:
            row = connection.execute("SELECT 1 AS ready").fetchone()
            return row is not None and row["ready"] == 1
        finally:
            self._close(connection)

    def upsert_seed_task(self, task: TaskRecord) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO tasks(
                    id, tenant_id, generation, version, task_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(tenant_id, id) DO UPDATE SET
                    generation=excluded.generation,
                    version=excluded.version,
                    task_json=excluded.task_json,
                    created_at=excluded.created_at,
                    updated_at=excluded.updated_at
                """,
                (
                    task.id,
                    self.tenant_id,
                    task.generation,
                    task.version,
                    Jsonb(task.model_dump(mode="json")),
                    task.created_at,
                    task.updated_at,
                ),
            )
            self._commit(connection)
        finally:
            self._close(connection)

    def create_task(self, task: TaskRecord) -> None:
        """Insert a user-owned task without overwriting an existing tenant task."""

        connection = self._connect()
        try:
            row = connection.execute(
                """
                INSERT INTO tasks(
                    id, tenant_id, generation, version, task_json, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(tenant_id, id) DO NOTHING
                RETURNING id
                """,
                (
                    task.id,
                    self.tenant_id,
                    task.generation,
                    task.version,
                    Jsonb(task.model_dump(mode="json")),
                    task.created_at,
                    task.updated_at,
                ),
            ).fetchone()
            if row is None:
                raise ConflictError(
                    "task_already_exists",
                    "A task with this id already exists; live task creation never overwrites it",
                    {"task_id": task.id},
                )
            self._commit(connection)
        finally:
            self._close(connection)

    def save_task(self, task: TaskRecord, expected_version: int) -> None:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE tasks
                   SET generation=%s, version=%s, task_json=%s, updated_at=%s
                 WHERE tenant_id=%s AND id=%s AND generation=%s AND version=%s
                """,
                (
                    task.generation,
                    task.version,
                    Jsonb(task.model_dump(mode="json")),
                    task.updated_at,
                    self.tenant_id,
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
        connection = self._connect()
        try:
            suffix = " FOR UPDATE" if self._active_transaction() is not None else ""
            row = connection.execute(
                "SELECT task_json FROM tasks WHERE tenant_id=%s AND id=%s" + suffix,
                (self.tenant_id, task_id),
            ).fetchone()
        finally:
            self._close(connection)
        if row is None:
            raise NotFoundError("task", task_id)
        return TaskRecord.model_validate(_json_object(row["task_json"]))

    def list_tasks(self) -> List[TaskRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT task_json FROM tasks
                 WHERE tenant_id=%s ORDER BY updated_at DESC, id
                """,
                (self.tenant_id,),
            ).fetchall()
        finally:
            self._close(connection)
        return [TaskRecord.model_validate(_json_object(row["task_json"])) for row in rows]

    def add_approval(self, approval: ApprovalRecord) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO approvals(
                    id, tenant_id, task_id, generation, status, expires_at, token_hash,
                    record_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    approval.id,
                    self.tenant_id,
                    approval.task_id,
                    approval.generation,
                    approval.status.value,
                    approval.expires_at,
                    approval.token_hash,
                    Jsonb(approval.model_dump(mode="json")),
                    approval.requested_at,
                ),
            )
            self._commit(connection)
        finally:
            self._close(connection)

    def save_approval(self, approval: ApprovalRecord) -> None:
        connection = self._connect()
        try:
            cursor = connection.execute(
                """
                UPDATE approvals
                   SET status=%s, expires_at=%s, token_hash=%s, record_json=%s
                 WHERE tenant_id=%s AND id=%s
                """,
                (
                    approval.status.value,
                    approval.expires_at,
                    approval.token_hash,
                    Jsonb(approval.model_dump(mode="json")),
                    self.tenant_id,
                    approval.id,
                ),
            )
            if cursor.rowcount != 1:
                raise NotFoundError("approval", approval.id)
            self._commit(connection)
        finally:
            self._close(connection)

    @staticmethod
    def _approval_from_row(row: Mapping[str, Any]) -> ApprovalRecord:
        approval = ApprovalRecord.model_validate(_json_object(row["record_json"]))
        approval.token_hash = row["token_hash"]
        return approval

    def get_approval(self, approval_id: str) -> ApprovalRecord:
        connection = self._connect()
        try:
            suffix = " FOR UPDATE" if self._active_transaction() is not None else ""
            row = connection.execute(
                "SELECT * FROM approvals WHERE tenant_id=%s AND id=%s" + suffix,
                (self.tenant_id, approval_id),
            ).fetchone()
        finally:
            self._close(connection)
        if row is None:
            raise NotFoundError("approval", approval_id)
        return self._approval_from_row(row)

    def latest_approval(self, task_id: str, generation: str) -> Optional[ApprovalRecord]:
        connection = self._connect()
        try:
            suffix = " FOR UPDATE" if self._active_transaction() is not None else ""
            row = connection.execute(
                """
                SELECT * FROM approvals
                 WHERE tenant_id=%s AND task_id=%s AND generation=%s
                 ORDER BY created_at DESC LIMIT 1
                """
                + suffix,
                (self.tenant_id, task_id, generation),
            ).fetchone()
        finally:
            self._close(connection)
        return self._approval_from_row(row) if row else None

    def approval_by_token_hash(self, token_hash: str) -> Optional[ApprovalRecord]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT * FROM approvals WHERE tenant_id=%s AND token_hash=%s",
                (self.tenant_id, token_hash),
            ).fetchone()
        finally:
            self._close(connection)
        return self._approval_from_row(row) if row else None

    def add_evidence(self, record: EvidenceRecord) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO evidence(
                    id, tenant_id, task_id, generation, kind, artifact_digest,
                    record_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.id,
                    self.tenant_id,
                    record.task_id,
                    record.generation,
                    record.kind.value,
                    record.artifact_digest,
                    Jsonb(record.model_dump(mode="json")),
                    record.created_at,
                ),
            )
            self._commit(connection)
        finally:
            self._close(connection)

    def list_evidence(self, task_id: str, generation: str) -> List[EvidenceRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT record_json FROM evidence
                 WHERE tenant_id=%s AND task_id=%s AND generation=%s
                 ORDER BY created_at, id
                """,
                (self.tenant_id, task_id, generation),
            ).fetchall()
        finally:
            self._close(connection)
        return [EvidenceRecord.model_validate(_json_object(row["record_json"])) for row in rows]

    def add_memory_candidate(self, record: MemoryCandidate) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO memory_candidates(
                    id, tenant_id, task_id, generation, evidence_digest, review_id,
                    record_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.id,
                    self.tenant_id,
                    record.task_id,
                    record.generation,
                    record.evidence_digest,
                    record.review_id,
                    Jsonb(record.model_dump(mode="json")),
                    record.created_at,
                ),
            )
            self._commit(connection)
        finally:
            self._close(connection)

    def list_memory_candidates(self, task_id: str, generation: str) -> List[MemoryCandidate]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT record_json FROM memory_candidates
                 WHERE tenant_id=%s AND task_id=%s AND generation=%s
                 ORDER BY created_at, id
                """,
                (self.tenant_id, task_id, generation),
            ).fetchall()
        finally:
            self._close(connection)
        return [MemoryCandidate.model_validate(_json_object(row["record_json"])) for row in rows]

    def add_memory(self, record: MemoryRecord) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO memories(
                    id, tenant_id, task_id, generation, validated, record_json, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.id,
                    self.tenant_id,
                    record.task_id,
                    record.generation,
                    record.validated,
                    Jsonb(record.model_dump(mode="json")),
                    record.created_at,
                ),
            )
            self._commit(connection)
        finally:
            self._close(connection)

    def list_memories(self, task_id: str, generation: str) -> List[MemoryRecord]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT record_json FROM memories
                 WHERE tenant_id=%s AND task_id=%s AND generation=%s
                 ORDER BY created_at, id
                """,
                (self.tenant_id, task_id, generation),
            ).fetchall()
        finally:
            self._close(connection)
        return [MemoryRecord.model_validate(_json_object(row["record_json"])) for row in rows]

    @staticmethod
    def _trusted_memory_event_from_row(row: Mapping[str, Any]) -> TrustedMemoryEvent:
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
    def _decision_closure_from_row(row: Mapping[str, Any]) -> DecisionClosureRecord:
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
        if tenant_id != self.tenant_id:
            raise ValueError("decision closure tenant_id does not match store tenant")
        validate_sha256_digest(closure_digest)
        if not project_id or not idempotency_key:
            raise ValueError("decision closure project and idempotency key must be non-empty")
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
            connection = self._connect()
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s || chr(31) || %s, 0))",
                (tenant_id, project_id),
            )
            existing_key = connection.execute(
                """
                SELECT * FROM trusted_memory_decision_closures
                 WHERE tenant_id=%s AND project_id=%s AND idempotency_key=%s
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
                 WHERE tenant_id=%s AND project_id=%s AND closure_digest=%s
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
                ) VALUES (%s, %s, %s, %s, %s, %s)
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
        if tenant_id != self.tenant_id:
            raise ValueError("decision closure tenant_id does not match store tenant")
        validate_sha256_digest(closure_digest)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM trusted_memory_decision_closures
                 WHERE tenant_id=%s AND project_id=%s AND closure_digest=%s
                """,
                (tenant_id, project_id, closure_digest),
            ).fetchone()
        finally:
            self._close(connection)
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
        if tenant_id != self.tenant_id:
            raise ValueError("trusted-memory tenant_id does not match store tenant")
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
            connection = self._connect()
            connection.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(%s || chr(31) || %s || chr(31) || %s, 0)
                )
                """,
                (tenant_id, project_id, lineage_id),
            )
            existing = connection.execute(
                """
                SELECT * FROM trusted_memory_history
                 WHERE tenant_id=%s AND project_id=%s AND lineage_id=%s
                   AND idempotency_key=%s
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
                 WHERE tenant_id=%s AND project_id=%s AND lineage_id=%s
                 FOR UPDATE
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
                 WHERE tenant_id=%s AND project_id=%s AND lineage_id=%s
                 FOR UPDATE
                """,
                (tenant_id, project_id, lineage_id),
            ).fetchone()
            sequence = int(stream["sequence"]) + 1 if stream else 1
            previous_hash = str(stream["stream_root"]) if stream else ZERO_HASH
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
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                ) VALUES (%s, %s, %s, %s, %s)
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
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (tenant_id, project_id, lineage_id, event_hash, closure_digest),
                )
            if isinstance(record, TrustedFact):
                if current is None:
                    connection.execute(
                        """
                        INSERT INTO trusted_memory_current(
                            tenant_id, project_id, lineage_id, revision_id, revision,
                            fact_digest, state, eligible, fact_bytes, fact_event_hash,
                            projection_event_hash
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, true, %s, %s, %s)
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
                            revision_id=%s, revision=%s, fact_digest=%s, state=%s,
                            eligible=true, fact_bytes=%s, fact_event_hash=%s,
                            projection_event_hash=%s
                         WHERE tenant_id=%s AND project_id=%s AND lineage_id=%s
                           AND projection_event_hash=%s
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
                        state=%s, eligible=%s, projection_event_hash=%s
                     WHERE tenant_id=%s AND project_id=%s AND lineage_id=%s
                       AND projection_event_hash=%s
                    """,
                    (
                        projected_state.value,
                        projected_state is MemoryState.VALIDATED,
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
                    tenant_id, project_id, lineage_id, sequence, event_hash, payload_json
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    tenant_id,
                    project_id,
                    lineage_id,
                    sequence,
                    event_hash,
                    Jsonb(outbox_payload),
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
        if tenant_id != self.tenant_id:
            raise NotFoundError("trusted_memory_event", event_hash)
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM trusted_memory_history
                 WHERE tenant_id=%s AND project_id=%s AND lineage_id=%s AND event_hash=%s
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
        if tenant_id != self.tenant_id:
            return []
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM trusted_memory_history
                 WHERE tenant_id=%s AND project_id=%s AND lineage_id=%s AND sequence>%s
                 ORDER BY sequence LIMIT %s
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
        if tenant_id != self.tenant_id:
            return None
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT * FROM trusted_memory_current
                 WHERE tenant_id=%s AND project_id=%s AND lineage_id=%s AND eligible IS TRUE
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
        if tenant_id != self.tenant_id:
            return ZERO_HASH
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT stream_root FROM trusted_memory_streams
                 WHERE tenant_id=%s AND project_id=%s AND lineage_id=%s
                """,
                (tenant_id, project_id, lineage_id),
            ).fetchone()
        finally:
            self._close(connection)
        return str(row["stream_root"]) if row else ZERO_HASH

    def verify_trusted_memory_stream(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lineage_id: str,
    ) -> bool:
        if tenant_id != self.tenant_id:
            return False
        previous_hash = ZERO_HASH
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

        connection = self._connect()
        try:
            stream = connection.execute(
                """
                SELECT sequence, stream_root FROM trusted_memory_streams
                 WHERE tenant_id=%s AND project_id=%s AND lineage_id=%s
                """,
                (tenant_id, project_id, lineage_id),
            ).fetchone()
        finally:
            self._close(connection)
        if stream is None:
            return last_sequence == 0 and previous_hash == ZERO_HASH
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
        if tenant_id != self.tenant_id:
            return []
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
        if timestamp.tzinfo is None:
            raise ValueError("audit event created_at must be timezone-aware")
        timestamp = timestamp.astimezone(timezone.utc)
        event_id = "evt_%s" % uuid.uuid4().hex
        connection = self._connect()
        try:
            connection.execute(
                """
                SELECT pg_advisory_xact_lock(
                    hashtextextended(%s || chr(31) || %s || chr(31) || %s, 0)
                )
                """,
                (self.tenant_id, task_id, generation),
            )
            previous_row = connection.execute(
                """
                SELECT event_hash FROM audit_events
                 WHERE tenant_id=%s AND task_id=%s AND generation=%s
                 ORDER BY sequence DESC LIMIT 1
                """,
                (self.tenant_id, task_id, generation),
            ).fetchone()
            previous_hash = str(previous_row["event_hash"]) if previous_row else ZERO_HASH
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
            row = connection.execute(
                """
                INSERT INTO audit_events(
                    id, tenant_id, task_id, generation, event_type, actor, stage,
                    payload_json, previous_hash, event_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING sequence
                """,
                (
                    event_id,
                    self.tenant_id,
                    task_id,
                    generation,
                    event_type,
                    actor,
                    stage.value if stage else None,
                    Jsonb(payload),
                    previous_hash,
                    event_hash,
                    timestamp,
                ),
            ).fetchone()
            if row is None:
                raise RuntimeError("PostgreSQL did not return an audit-event sequence")
            sequence = int(row["sequence"])
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
    def _event_from_row(row: Mapping[str, Any]) -> AuditEvent:
        created_at = row["created_at"]
        if isinstance(created_at, str):
            created_at = datetime.fromisoformat(created_at)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        else:
            created_at = created_at.astimezone(timezone.utc)
        return AuditEvent(
            sequence=int(row["sequence"]),
            id=str(row["id"]),
            task_id=str(row["task_id"]),
            generation=str(row["generation"]),
            event_type=str(row["event_type"]),
            actor=str(row["actor"]),
            stage=Stage(row["stage"]) if row["stage"] else None,
            payload=_json_object(row["payload_json"]),
            previous_hash=str(row["previous_hash"]),
            event_hash=str(row["event_hash"]),
            created_at=created_at,
        )

    def list_events(
        self, task_id: str, generation: str, after_sequence: int = 0, limit: int = 200
    ) -> List[AuditEvent]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT * FROM audit_events
                 WHERE tenant_id=%s AND task_id=%s AND generation=%s AND sequence>%s
                 ORDER BY sequence LIMIT %s
                """,
                (self.tenant_id, task_id, generation, after_sequence, limit),
            ).fetchall()
        finally:
            self._close(connection)
        return [self._event_from_row(row) for row in rows]

    def verify_event_chain(self, task_id: str, generation: str) -> bool:
        previous_hash = ZERO_HASH
        after_sequence = 0
        while True:
            events = self.list_events(
                task_id, generation, after_sequence=after_sequence, limit=1000
            )
            if not events:
                break
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
                after_sequence = event.sequence
        return True

    def recent_events(
        self,
        limit: int = 30,
        task_id: Optional[str] = None,
        generation: Optional[str] = None,
    ) -> List[AuditEvent]:
        connection = self._connect()
        try:
            if task_id is not None and generation is not None:
                rows = connection.execute(
                    """
                    SELECT * FROM audit_events
                     WHERE tenant_id=%s AND task_id=%s AND generation=%s
                     ORDER BY sequence DESC LIMIT %s
                    """,
                    (self.tenant_id, task_id, generation, limit),
                ).fetchall()
            else:
                rows = connection.execute(
                    """
                    SELECT * FROM audit_events WHERE tenant_id=%s
                     ORDER BY created_at DESC, id DESC LIMIT %s
                    """,
                    (self.tenant_id, limit),
                ).fetchall()
        finally:
            self._close(connection)
        return [self._event_from_row(row) for row in reversed(rows)]

    def get_idempotent(
        self, method: str, path: str, key: str, request_hash: str
    ) -> Optional[Tuple[int, Dict[str, Any]]]:
        connection = self._connect()
        try:
            if self._active_transaction() is not None:
                connection.execute(
                    """
                    SELECT pg_advisory_xact_lock(
                        hashtextextended(%s || chr(31) || %s || chr(31) || %s || chr(31) || %s, 0)
                    )
                    """,
                    (self.tenant_id, method, path, key),
                )
            row = connection.execute(
                """
                SELECT request_hash, response_json, status_code FROM idempotency
                 WHERE tenant_id=%s AND method=%s AND path=%s AND key=%s
                """,
                (self.tenant_id, method, path, key),
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
        return int(row["status_code"]), _json_object(row["response_json"])

    def put_idempotent(
        self,
        method: str,
        path: str,
        key: str,
        request_hash: str,
        status_code: int,
        response: Dict[str, Any],
    ) -> None:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                INSERT INTO idempotency(
                    tenant_id, method, path, key, request_hash,
                    response_json, status_code, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(tenant_id, method, path, key) DO NOTHING
                RETURNING key
                """,
                (
                    self.tenant_id,
                    method,
                    path,
                    key,
                    request_hash,
                    Jsonb(response),
                    status_code,
                    utc_now(),
                ),
            ).fetchone()
            if row is None:
                existing = self.get_idempotent(method, path, key, request_hash)
                if existing is None:
                    raise RuntimeError("idempotency conflict row disappeared")
            self._commit(connection)
        finally:
            self._close(connection)

    def counts(self) -> Dict[str, int]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT
                    (SELECT count(*) FROM tasks WHERE tenant_id=%s) AS tasks,
                    (SELECT count(*) FROM approvals WHERE tenant_id=%s) AS approvals,
                    (SELECT count(*) FROM evidence WHERE tenant_id=%s) AS evidence,
                    (SELECT count(*) FROM memory_candidates
                      WHERE tenant_id=%s) AS memory_candidates,
                    (SELECT count(*) FROM audit_events WHERE tenant_id=%s) AS events,
                    (SELECT count(*) FROM memories
                      WHERE tenant_id=%s AND validated IS TRUE) AS validated_memories
                """,
                (self.tenant_id,) * 6,
            ).fetchone()
        finally:
            self._close(connection)
        if row is None:
            raise RuntimeError("PostgreSQL did not return store counts")
        return {
            name: int(row[name])
            for name in (
                "tasks",
                "approvals",
                "evidence",
                "memory_candidates",
                "events",
                "validated_memories",
            )
        }

    @contextmanager
    def stage_event_listener(self) -> Iterator[Connection[Dict[str, Any]]]:
        """Yield a LISTEN connection; consume ``connection.notifies()`` after yielding."""

        connection = self._new_connection(autocommit=True)
        connection.execute("LISTEN %s" % STAGE_EVENT_CHANNEL)
        try:
            yield connection
        finally:
            connection.close()
