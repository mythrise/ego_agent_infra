"""Fail-closed PostgreSQL/PolarDB live preflight and acceptance CLI.

The default ``preflight`` command performs catalog reads only.  It never creates a
schema, table, role, extension, backup, or cloud resource.  Transient NOTIFY and
rolled-back temporary-table probes require explicit flags.  The only destructive
operation implemented here, fresh-schema replay, has a deliberately redundant gate:
an isolated non-production database name, a database COMMENT marker, manifest
authorization, and exact command-line confirmations must all agree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence
from urllib.parse import urlsplit

import psycopg
from psycopg import Connection, sql
from psycopg.rows import dict_row

from .postgres_store import PostgresStore


REPORT_SCHEMA = "egoagentos.polardb-preflight/v1"
MANIFEST_SCHEMA = "egoagentos.polardb-live-acceptance/v1"
DISPOSABLE_MARKER = "EGOAGENTOS_DISPOSABLE_DATABASE_V1"
DEFAULT_DATABASE_PREFIX = "egoagentos_acceptance_"
NOTIFY_CHANNEL = "ego_polardb_preflight"
ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]*$")
SAFE_DATABASE = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_-]{2,62}$")
SAFE_PREFIX = re.compile(r"^[a-zA-Z0-9_][a-zA-Z0-9_-]{7,62}$")
EXPECTED_TABLES = (
    "approvals",
    "audit_events",
    "evidence",
    "idempotency",
    "memory_candidates",
    "memories",
    "tasks",
)
PRIVILEGE_TABLES = EXPECTED_TABLES + ("schema_migrations",)
EXPECTED_RUNTIME_UPDATE_COLUMNS = {
    "approvals": ("expires_at", "record_json", "status", "token_hash"),
    "tasks": ("created_at", "generation", "task_json", "updated_at", "version"),
}
EXPECTED_RLS_TABLES = EXPECTED_TABLES
EXPECTED_POLICIES = tuple("%s_tenant_policy" % table for table in EXPECTED_RLS_TABLES)
EXPECTED_TRIGGERS = (
    "audit_events_guard_insert",
    "audit_events_no_truncate",
    "audit_events_no_update_or_delete",
    "audit_events_stage_notify",
    "evidence_no_truncate",
    "evidence_no_update_or_delete",
    "memory_candidates_no_truncate",
    "memory_candidates_no_update_or_delete",
    "memories_no_truncate",
    "memories_no_update_or_delete",
)
EXPECTED_TRIGGER_FUNCTIONS = {
    "audit_events_guard_insert": "egoagentos_guard_audit_insert",
    "audit_events_no_truncate": "egoagentos_reject_audit_mutation",
    "audit_events_no_update_or_delete": "egoagentos_reject_audit_mutation",
    "audit_events_stage_notify": "egoagentos_notify_stage_event",
    "evidence_no_truncate": "egoagentos_reject_ledger_mutation",
    "evidence_no_update_or_delete": "egoagentos_reject_ledger_mutation",
    "memory_candidates_no_truncate": "egoagentos_reject_ledger_mutation",
    "memory_candidates_no_update_or_delete": "egoagentos_reject_ledger_mutation",
    "memories_no_truncate": "egoagentos_reject_ledger_mutation",
    "memories_no_update_or_delete": "egoagentos_reject_ledger_mutation",
}
ROLE_KEYS = ("runtime", "auditor", "evidence_writer", "memory_curator")
DESTRUCTIVE_OPERATIONS = ("fresh_schema_replay", "pitr_restore", "multi_az_failover")


class ManifestError(ValueError):
    """The operator-supplied acceptance manifest is unsafe or malformed."""


class SafetyGateError(RuntimeError):
    """A destructive action failed one or more independent confirmations."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _safe_location(database_url: str) -> str:
    parsed = urlsplit(database_url)
    host = parsed.hostname or "unknown-host"
    port = ":%s" % parsed.port if parsed.port else ""
    database = parsed.path.lstrip("/") or "unknown-database"
    return "%s%s/%s" % (host, port, database)


def _redact_error(error: BaseException, secrets: Iterable[str] = ()) -> str:
    message = "%s: %s" % (type(error).__name__, str(error))
    for secret in secrets:
        if secret:
            message = message.replace(secret, "<redacted-database-url>")
    message = re.sub(r"(postgres(?:ql)?://)[^\s/@:]+(?::[^\s/@]*)?@", r"\1<redacted>@", message)
    message = re.sub(r"(?i)(password\s*=\s*)[^\s]+", r"\1<redacted>", message)
    return message[:600]


def _check(
    status: str,
    message: str,
    *,
    required: bool,
    evidence: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "status": status,
        "required": required,
        "message": message,
        "evidence": dict(evidence or {}),
    }


def _checks(value: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        if {"status", "required", "message"}.issubset(value):
            yield value
        else:
            for child in value.values():
                yield from _checks(child)
    elif isinstance(value, list):
        for child in value:
            yield from _checks(child)


def _summarize(checks: Mapping[str, Any]) -> Dict[str, Any]:
    counts = {name: 0 for name in ("PASS", "WARN", "FAIL", "ERROR", "SKIP")}
    required_failures = 0
    for item in _checks(checks):
        status = str(item["status"])
        counts[status] = counts.get(status, 0) + 1
        if bool(item["required"]) and status in {"FAIL", "ERROR", "SKIP"}:
            required_failures += 1
    if required_failures:
        status = "FAIL"
    elif counts["WARN"] or counts["SKIP"] or counts["FAIL"] or counts["ERROR"]:
        status = "PASS_WITH_GAPS"
    else:
        status = "PASS"
    return {"status": status, "required_failures": required_failures, "counts": counts}


def load_manifest(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ManifestError("cannot read acceptance manifest: %s" % error) from error
    if not isinstance(value, dict):
        raise ManifestError("acceptance manifest must be a JSON object")
    if value.get("schema_version") != MANIFEST_SCHEMA:
        raise ManifestError("schema_version must be %s" % MANIFEST_SCHEMA)
    target = value.get("target")
    operations = value.get("operations")
    if not isinstance(target, dict) or not isinstance(operations, dict):
        raise ManifestError("manifest requires target and operations objects")
    environment = target.get("environment")
    if environment not in {"nonproduction", "staging", "production"}:
        raise ManifestError("target.environment must be nonproduction, staging, or production")
    expected_database = target.get("expected_database")
    if not isinstance(expected_database, str) or not SAFE_DATABASE.fullmatch(expected_database):
        raise ManifestError("target.expected_database must be an explicit safe database name")
    for key in (
        "writer_url_env",
        "reader_url_env",
        "runtime_url_env",
        "auditor_url_env",
        "evidence_writer_url_env",
        "memory_curator_url_env",
    ):
        name = target.get(key)
        if name is not None and (not isinstance(name, str) or not ENV_NAME.fullmatch(name)):
            raise ManifestError("target.%s must name an uppercase environment variable" % key)
    if not target.get("writer_url_env"):
        raise ManifestError("target.writer_url_env is required")
    minimum = target.get("minimum_server_version_num", 120000)
    if not isinstance(minimum, int) or minimum < 110000:
        raise ManifestError("minimum_server_version_num must be an integer >= 110000")
    pgvector = target.get("pgvector", "optional")
    if pgvector not in {"optional", "required", "disabled"}:
        raise ManifestError("target.pgvector must be optional, required, or disabled")
    roles = target.get("roles", {})
    if not isinstance(roles, dict):
        raise ManifestError("target.roles must be an object")
    for key in ROLE_KEYS:
        role = roles.get(key)
        if not isinstance(role, str) or not SAFE_DATABASE.fullmatch(role):
            raise ManifestError("target.roles.%s must be an explicit role name" % key)
    for key in (
        "require_tls",
        "require_polardb_marker",
        "require_read_endpoint",
        "require_rls",
        "require_force_rls",
        "require_role_logins",
    ):
        if not isinstance(target.get(key), bool):
            raise ManifestError("target.%s must be a boolean" % key)
    prefix = target.get("disposable_database_prefix", DEFAULT_DATABASE_PREFIX)
    if not isinstance(prefix, str) or not SAFE_PREFIX.fullmatch(prefix):
        raise ManifestError(
            "target.disposable_database_prefix must be an explicit safe prefix of at least 8 characters"
        )
    if target.get("disposable_database_marker", DISPOSABLE_MARKER) != DISPOSABLE_MARKER:
        raise ManifestError("target.disposable_database_marker must be %s" % DISPOSABLE_MARKER)
    for operation in ("fresh_schema_replay", "pitr_restore", "multi_az_failover"):
        operation_value = operations.get(operation)
        if not isinstance(operation_value, dict):
            raise ManifestError("operations.%s must be an object" % operation)
        if not isinstance(operation_value.get("authorized"), bool):
            raise ManifestError("operations.%s.authorized must be a boolean" % operation)
        if not isinstance(operation_value.get("status"), str):
            raise ManifestError("operations.%s.status must be a string" % operation)
    if any(
        operations[operation]["authorized"] for operation in ("fresh_schema_replay", "pitr_restore")
    ) and not expected_database.startswith(prefix):
        raise ManifestError(
            "authorized destructive target must begin with disposable_database_prefix"
        )
    return value


def _packaged_migrations() -> Dict[str, str]:
    migration_root = resources.files("apps.api").joinpath("migrations/postgres")
    result: Dict[str, str] = {}
    for entry in migration_root.iterdir():
        if entry.name.endswith(".sql"):
            contents = entry.read_text(encoding="utf-8")
            result[entry.name] = hashlib.sha256(contents.encode("utf-8")).hexdigest()
    return result


def _database_url_from_env(target: Mapping[str, Any], key: str, environ: Mapping[str, str]) -> str:
    env_name = target.get(key)
    if not env_name:
        return ""
    value = environ.get(str(env_name), "").strip()
    if not value:
        return ""
    if not value.startswith(("postgresql://", "postgres://")):
        raise ManifestError("%s must contain a postgresql:// or postgres:// URL" % env_name)
    return value


class PostgresInspector:
    """Read-only catalog inspector with separately opted-in transient probes."""

    @staticmethod
    def _connect(database_url: str) -> Connection[Dict[str, Any]]:
        return psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
            application_name="egoagentos-polardb-preflight",
        )

    def inspect_endpoint(self, database_url: str, label: str) -> Dict[str, Any]:
        connection = self._connect(database_url)
        try:
            # The first statement is a fixed SELECT so endpoint-enforced read-only state is
            # observable.  The session is then forced read-only before every other probe.
            identity = connection.execute(
                """
                /* egoagentos_preflight:identity */
                SELECT version() AS advertised_version,
                       current_setting('server_version') AS server_version,
                       current_setting('server_version_num')::integer AS server_version_num,
                       current_setting('transaction_read_only')::boolean AS endpoint_read_only,
                       pg_is_in_recovery() AS in_recovery,
                       current_database() AS database_name,
                       current_user AS current_user,
                       inet_server_addr()::text AS server_address,
                       inet_server_port() AS server_port,
                       current_setting('polar_node_type', true) AS polar_node_type
                """
            ).fetchone()
            if identity is None:
                raise RuntimeError("identity probe returned no row")
            connection.execute("SET default_transaction_read_only = on")
            connection.execute("SET TIME ZONE 'UTC'")
            tls = connection.execute(
                """
                /* egoagentos_preflight:tls */
                SELECT COALESCE(ssl, false) AS ssl,
                       version AS tls_version,
                       cipher
                  FROM pg_stat_ssl WHERE pid=pg_backend_pid()
                """
            ).fetchone()
            jsonb = connection.execute(
                """
                /* egoagentos_preflight:jsonb */
                SELECT jsonb_build_object('probe', 'egoagentos', 'ok', true)
                       @> '{"ok": true}'::jsonb AS supported
                """
            ).fetchone()
            polar_rows = connection.execute(
                """
                /* egoagentos_preflight:polar_settings */
                SELECT name FROM pg_settings
                 WHERE name LIKE 'polar%' ORDER BY name LIMIT 64
                """
            ).fetchall()
            vector = connection.execute(
                """
                /* egoagentos_preflight:pgvector */
                SELECT available.name, available.default_version, installed.extversion
                  FROM pg_available_extensions AS available
                  LEFT JOIN pg_extension AS installed ON installed.extname=available.name
                 WHERE available.name='vector'
                """
            ).fetchone()
            marker = connection.execute(
                """
                /* egoagentos_preflight:database_marker */
                SELECT shobj_description(oid, 'pg_database') AS database_comment
                  FROM pg_database WHERE datname=current_database()
                """
            ).fetchone()
        finally:
            connection.close()
        polar_setting_names = [str(row["name"]) for row in polar_rows]
        advertised = str(identity["advertised_version"])
        polar_marker = bool(
            "polardb" in advertised.lower()
            or polar_setting_names
            or identity.get("polar_node_type")
        )
        return {
            "label": label,
            "location": _safe_location(database_url),
            "identity": dict(identity),
            "tls": dict(tls or {"ssl": False, "tls_version": None, "cipher": None}),
            "jsonb_supported": bool(jsonb and jsonb["supported"]),
            "polar_marker_observed": polar_marker,
            "polar_setting_names": polar_setting_names,
            "pgvector": dict(vector) if vector else None,
            "database_comment": marker.get("database_comment") if marker else None,
            "session_forced_read_only_after_identity": True,
        }

    def inspect_control_plane(self, database_url: str, roles: Mapping[str, str]) -> Dict[str, Any]:
        connection = self._connect(database_url)
        try:
            connection.execute("SET default_transaction_read_only = on")
            tables = connection.execute(
                """
                /* egoagentos_preflight:tables */
                SELECT c.relname AS table_name, c.relrowsecurity, c.relforcerowsecurity
                  FROM pg_class AS c JOIN pg_namespace AS n ON n.oid=c.relnamespace
                 WHERE n.nspname='public' AND c.relkind='r'
                   AND c.relname = ANY(%s) ORDER BY c.relname
                """,
                (list(EXPECTED_TABLES),),
            ).fetchall()
            triggers = connection.execute(
                """
                /* egoagentos_preflight:triggers */
                SELECT t.tgname AS trigger_name,
                       t.tgenabled AS trigger_enabled,
                       p.proname AS function_name
                  FROM pg_trigger AS t
                  JOIN pg_class AS c ON c.oid=t.tgrelid
                  JOIN pg_namespace AS n ON n.oid=c.relnamespace
                  JOIN pg_proc AS p ON p.oid=t.tgfoid
                 WHERE n.nspname='public'
                   AND c.relname = ANY(%s)
                   AND NOT t.tgisinternal ORDER BY t.tgname
                """,
                (["audit_events", "evidence", "memory_candidates", "memories"],),
            ).fetchall()
            policies = connection.execute(
                """
                /* egoagentos_preflight:policies */
                SELECT tablename, policyname, cmd, qual, with_check
                  FROM pg_policies
                 WHERE schemaname='public' AND tablename = ANY(%s)
                 ORDER BY tablename, policyname
                """,
                (list(EXPECTED_RLS_TABLES),),
            ).fetchall()
            migrations = connection.execute(
                """
                /* egoagentos_preflight:migrations */
                SELECT version, sha256 FROM schema_migrations ORDER BY version
                """
            ).fetchall()
            role_rows = connection.execute(
                """
                /* egoagentos_preflight:roles */
                SELECT rolname FROM pg_roles WHERE rolname = ANY(%s) ORDER BY rolname
                """,
                (list(roles.values()),),
            ).fetchall()
            existing_roles = {str(row["rolname"]) for row in role_rows}
            privileges: Dict[str, Dict[str, Dict[str, Any]]] = {}
            for role in roles.values():
                if role not in existing_roles:
                    continue
                role_privileges: Dict[str, Dict[str, Any]] = {}
                for table in PRIVILEGE_TABLES:
                    row = connection.execute(
                        """
                        /* egoagentos_preflight:privileges */
                        SELECT has_table_privilege(%s, %s, 'SELECT') AS select,
                               has_table_privilege(%s, %s, 'INSERT') AS insert,
                               has_table_privilege(%s, %s, 'UPDATE') AS update,
                               has_table_privilege(%s, %s, 'DELETE') AS delete
                        """,
                        (
                            role,
                            "public.%s" % table,
                            role,
                            "public.%s" % table,
                            role,
                            "public.%s" % table,
                            role,
                            "public.%s" % table,
                        ),
                    ).fetchone()
                    role_privileges[table] = {
                        name: bool(row and row[name])
                        for name in ("select", "insert", "update", "delete")
                    }
                    column_rows = connection.execute(
                        """
                        /* egoagentos_preflight:update_columns */
                        SELECT column_name,
                               has_column_privilege(%s, %s, column_name, 'UPDATE') AS update
                          FROM information_schema.columns
                         WHERE table_schema='public' AND table_name=%s
                         ORDER BY ordinal_position
                        """,
                        (role, "public.%s" % table, table),
                    ).fetchall()
                    role_privileges[table]["update_columns"] = {
                        str(column_row["column_name"]): bool(column_row["update"])
                        for column_row in column_rows
                    }
                privileges[role] = role_privileges
        finally:
            connection.close()
        return {
            "tables": [dict(row) for row in tables],
            "triggers": [dict(row) for row in triggers],
            "policies": [dict(row) for row in policies],
            "migrations": [dict(row) for row in migrations],
            "roles": sorted(existing_roles),
            "privileges": privileges,
        }

    def inspect_login(
        self, database_url: str, expected_capability_group: str
    ) -> Dict[str, Any]:
        connection = self._connect(database_url)
        try:
            connection.execute("SET default_transaction_read_only = on")
            row = connection.execute(
                """
                /* egoagentos_preflight:login */
                SELECT session_user AS login_identity,
                       current_user AS current_user,
                       current_database() AS database_name,
                       COALESCE(
                           (SELECT rolcanlogin FROM pg_roles WHERE rolname=session_user),
                           false
                       ) AS login_can_login,
                       session_user <> %s AS dedicated_login,
                       COALESCE(
                           (
                               SELECT NOT capability.rolcanlogin
                                 FROM pg_roles capability
                                WHERE capability.rolname=%s
                           ),
                           false
                       ) AS capability_group_nologin,
                       COALESCE(
                           (
                               SELECT pg_has_role(session_user, capability.oid, 'MEMBER')
                                 FROM pg_roles capability
                                WHERE capability.rolname=%s
                           ),
                           false
                       ) AS capability_group_member
                """,
                (
                    expected_capability_group,
                    expected_capability_group,
                    expected_capability_group,
                ),
            ).fetchone()
            tls = connection.execute(
                "SELECT COALESCE(ssl, false) AS ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
            ).fetchone()
        finally:
            connection.close()
        if row is None:
            raise RuntimeError("login probe returned no row")
        return {
            "login_identity": row["login_identity"],
            "current_user": row["current_user"],
            "database_name": row["database_name"],
            "login_can_login": bool(row["login_can_login"]),
            "dedicated_login": bool(row["dedicated_login"]),
            "capability_group": expected_capability_group,
            "capability_group_nologin": bool(row["capability_group_nologin"]),
            "capability_group_member": bool(row["capability_group_member"]),
            "tls": bool(tls and tls["ssl"]),
        }

    def active_notify(self, database_url: str, timeout: float = 2.0) -> Dict[str, Any]:
        payload = "ego-preflight-%s" % uuid.uuid4().hex
        listener = self._connect(database_url)
        publisher = self._connect(database_url)
        try:
            listener.execute(sql.SQL("LISTEN {}").format(sql.Identifier(NOTIFY_CHANNEL)))
            publisher.execute("SELECT pg_notify(%s, %s)", (NOTIFY_CHANNEL, payload))
            notifications = list(listener.notifies(timeout=timeout, stop_after=1))
        finally:
            listener.close()
            publisher.close()
        matched = len(notifications) == 1 and notifications[0].payload == payload
        return {"matched": matched, "received": len(notifications), "channel": NOTIFY_CHANNEL}

    def active_topology(self, database_url: str) -> Dict[str, Any]:
        """Try a transaction-local TEMP table and always roll it back."""

        connection = self._connect(database_url)
        accepted = False
        error_type: Optional[str] = None
        try:
            connection.execute("BEGIN")
            try:
                connection.execute(
                    "CREATE TEMP TABLE egoagentos_preflight_write_probe(value integer) ON COMMIT DROP"
                )
                connection.execute("INSERT INTO egoagentos_preflight_write_probe VALUES (1)")
                accepted = True
            except Exception as error:  # noqa: BLE001 - the error class is evidence
                error_type = type(error).__name__
            finally:
                connection.execute("ROLLBACK")
        finally:
            connection.close()
        return {"temporary_write_accepted": accepted, "error_type": error_type, "rolled_back": True}

    def disposable_target(self, database_url: str) -> Dict[str, Any]:
        endpoint = self.inspect_endpoint(database_url, "destructive-target")
        return {
            "database_name": endpoint["identity"]["database_name"],
            "database_comment": endpoint["database_comment"],
            "location": endpoint["location"],
            "tls": bool(endpoint["tls"]["ssl"]),
        }

    def fresh_schema_replay(
        self,
        database_url: str,
        *,
        expected_database: str,
        expected_marker: str,
        require_tls: bool,
    ) -> Dict[str, Any]:
        connection = self._connect(database_url)
        try:
            connection.execute("BEGIN")
            try:
                connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    ("egoagentos:fresh-schema-replay:%s" % expected_database,),
                )
                live_target = connection.execute(
                    """
                    SELECT current_database() AS database_name,
                           shobj_description(oid, 'pg_database') AS database_comment,
                           COALESCE(
                               (SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()),
                               false
                           ) AS tls
                      FROM pg_database WHERE datname=current_database()
                    """
                ).fetchone()
                if live_target is None:
                    raise SafetyGateError("destructive transaction target could not be identified")
                if (
                    str(live_target["database_name"]) != expected_database
                    or live_target["database_comment"] != expected_marker
                ):
                    raise SafetyGateError(
                        "destructive transaction target changed after the safety checks"
                    )
                if require_tls and live_target["tls"] is not True:
                    raise SafetyGateError("destructive transaction no longer has verified TLS")
                connection.execute("DROP SCHEMA public CASCADE")
                connection.execute("CREATE SCHEMA public")
            except Exception:
                connection.execute("ROLLBACK")
                raise
            else:
                connection.execute("COMMIT")
        finally:
            connection.close()
        PostgresStore(database_url, migration_mode="apply")
        verification = self._connect(database_url)
        try:
            migrations = verification.execute(
                "SELECT version, sha256 FROM schema_migrations ORDER BY version"
            ).fetchall()
            tables = verification.execute(
                """
                SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename
                """
            ).fetchall()
        finally:
            verification.close()
        return {
            "migrations": [dict(row) for row in migrations],
            "tables": [str(row["tablename"]) for row in tables],
            "security_roles_reapply_required": True,
            "target_reverified_in_destructive_transaction": True,
        }


def _role_matrix_ok(
    privileges: Mapping[str, Mapping[str, Mapping[str, Any]]],
    roles: Mapping[str, str],
) -> Dict[str, Any]:
    runtime_role = roles["runtime"]
    auditor_role = roles["auditor"]
    evidence_writer_role = roles["evidence_writer"]
    memory_curator_role = roles["memory_curator"]
    runtime = privileges.get(runtime_role, {})
    auditor = privileges.get(auditor_role, {})
    evidence_writer = privileges.get(evidence_writer_role, {})
    memory_curator = privileges.get(memory_curator_role, {})
    failures: List[str] = []
    runtime_write = {
        "tasks": {"insert"},
        "approvals": {"insert"},
        "evidence": {"insert"},
        "memory_candidates": {"insert"},
        "memories": {"insert"},
        "audit_events": {"insert"},
        "idempotency": {"insert"},
        "schema_migrations": set(),
    }
    evidence_writer_read = {"tasks", "approvals", "evidence"}
    memory_curator_read = {"tasks", "evidence", "memory_candidates", "memories"}
    observed_runtime_update_columns: Dict[str, List[str]] = {}

    def update_columns(row: Mapping[str, Any]) -> set[str]:
        raw = row.get("update_columns", {})
        if not isinstance(raw, Mapping):
            return set()
        return {str(column) for column, granted in raw.items() if granted}

    for table in PRIVILEGE_TABLES:
        row = runtime.get(table, {})
        if not row.get("select"):
            failures.append("%s lacks SELECT on %s" % (runtime_role, table))
        for action in ("insert", "update", "delete"):
            expected = action in runtime_write[table]
            if bool(row.get(action)) != expected:
                failures.append("%s %s on %s expected %s" % (runtime_role, action, table, expected))
        observed_update = update_columns(row)
        expected_update = set(EXPECTED_RUNTIME_UPDATE_COLUMNS.get(table, ()))
        observed_runtime_update_columns[table] = sorted(observed_update)
        if observed_update != expected_update:
            failures.append(
                "%s UPDATE columns on %s expected %s, observed %s"
                % (
                    runtime_role,
                    table,
                    sorted(expected_update),
                    sorted(observed_update),
                )
            )
        auditor_row = auditor.get(table, {})
        if not auditor_row.get("select"):
            failures.append("%s lacks SELECT on %s" % (auditor_role, table))
        if any(auditor_row.get(action) for action in ("insert", "update", "delete")):
            failures.append("%s has mutation privilege on %s" % (auditor_role, table))
        if update_columns(auditor_row):
            failures.append("%s has column UPDATE privilege on %s" % (auditor_role, table))
        evidence_row = evidence_writer.get(table, {})
        if bool(evidence_row.get("select")) != (table in evidence_writer_read):
            failures.append(
                "%s SELECT on %s expected %s"
                % (evidence_writer_role, table, table in evidence_writer_read)
            )
        for action in ("insert", "update", "delete"):
            expected = action == "insert" and table == "evidence"
            if bool(evidence_row.get(action)) != expected:
                failures.append(
                    "%s %s on %s expected %s" % (evidence_writer_role, action, table, expected)
                )
        if update_columns(evidence_row):
            failures.append("%s has column UPDATE privilege on %s" % (evidence_writer_role, table))
        curator_row = memory_curator.get(table, {})
        if bool(curator_row.get("select")) != (table in memory_curator_read):
            failures.append(
                "%s SELECT on %s expected %s"
                % (memory_curator_role, table, table in memory_curator_read)
            )
        for action in ("insert", "update", "delete"):
            expected = action == "insert" and table == "memory_candidates"
            if bool(curator_row.get(action)) != expected:
                failures.append(
                    "%s %s on %s expected %s" % (memory_curator_role, action, table, expected)
                )
        if update_columns(curator_row):
            failures.append("%s has column UPDATE privilege on %s" % (memory_curator_role, table))
    return {
        "ok": not failures,
        "failures": failures,
        "runtime_update_columns": observed_runtime_update_columns,
    }


def _endpoint_checks(
    endpoint: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    label: str,
) -> Dict[str, Any]:
    identity = endpoint["identity"]
    expected_database = str(target["expected_database"])
    minimum_version = int(target.get("minimum_server_version_num", 120000))
    require_tls = bool(target.get("require_tls", True))
    require_polardb = bool(target.get("require_polardb_marker", False))
    is_writer = label == "writer"
    checks: Dict[str, Any] = {
        "connection": _check(
            "PASS",
            "%s endpoint accepted a read-only catalog session" % label,
            required=True,
            evidence={"location": endpoint["location"], "database": identity["database_name"]},
        ),
        "database_identity": _check(
            "PASS" if identity["database_name"] == expected_database else "FAIL",
            "connected database must match manifest exactly",
            required=True,
            evidence={"expected": expected_database, "observed": identity["database_name"]},
        ),
        "engine_version": _check(
            "PASS" if int(identity["server_version_num"]) >= minimum_version else "FAIL",
            "PostgreSQL-compatible server version meets the manifest floor",
            required=True,
            evidence={
                "advertised_version": identity["advertised_version"],
                "server_version": identity["server_version"],
                "server_version_num": identity["server_version_num"],
                "minimum": minimum_version,
            },
        ),
        "tls": _check(
            "PASS" if bool(endpoint["tls"]["ssl"]) else ("FAIL" if require_tls else "WARN"),
            "the current database session is TLS encrypted",
            required=require_tls,
            evidence=endpoint["tls"],
        ),
        "polardb_identity": _check(
            "PASS"
            if endpoint["polar_marker_observed"]
            else ("FAIL" if require_polardb else "WARN"),
            "PolarDB identity requires an advertised or pg_settings marker; wire compatibility alone is insufficient",
            required=require_polardb,
            evidence={
                "marker_observed": endpoint["polar_marker_observed"],
                "polar_setting_names": endpoint["polar_setting_names"],
                "polar_node_type": identity.get("polar_node_type"),
            },
        ),
        "jsonb": _check(
            "PASS" if endpoint["jsonb_supported"] else "FAIL",
            "JSONB construction and containment operator are available",
            required=True,
        ),
    }
    if is_writer:
        writer_ok = not bool(identity["endpoint_read_only"]) and not bool(identity["in_recovery"])
        checks["endpoint_role"] = _check(
            "PASS" if writer_ok else "FAIL",
            "writer endpoint must not advertise read-only or recovery mode",
            required=True,
            evidence={
                "endpoint_read_only": identity["endpoint_read_only"],
                "in_recovery": identity["in_recovery"],
            },
        )
    else:
        node_type = str(identity.get("polar_node_type") or "").lower()
        reader_ok = (
            bool(identity["endpoint_read_only"])
            or bool(identity["in_recovery"])
            or node_type
            in {
                "ro",
                "reader",
                "read_only",
                "replica",
            }
        )
        required = bool(target.get("require_read_endpoint", False))
        checks["endpoint_role"] = _check(
            "PASS" if reader_ok else ("FAIL" if required else "WARN"),
            "reader endpoint must advertise a read-only, recovery, or vendor reader state",
            required=required,
            evidence={
                "endpoint_read_only": identity["endpoint_read_only"],
                "in_recovery": identity["in_recovery"],
                "polar_node_type": identity.get("polar_node_type"),
            },
        )
    return checks


def run_preflight(
    manifest: Mapping[str, Any],
    environ: Mapping[str, str],
    *,
    inspector: Optional[PostgresInspector] = None,
    active_notify: bool = False,
    active_topology: bool = False,
    allow_production_readonly: bool = False,
) -> Dict[str, Any]:
    target = manifest["target"]
    environment = str(target["environment"])
    if environment == "production" and not allow_production_readonly:
        raise ManifestError("production preflight requires --allow-production-readonly")
    if environment == "production" and (active_notify or active_topology):
        raise ManifestError("production manifests only permit the read-only preflight mode")
    probe = inspector or PostgresInspector()
    writer_url = _database_url_from_env(target, "writer_url_env", environ)
    if not writer_url:
        raise ManifestError("writer URL environment variable is unset")
    reader_url = _database_url_from_env(target, "reader_url_env", environ)
    role_urls = {
        "runtime": _database_url_from_env(target, "runtime_url_env", environ),
        "auditor": _database_url_from_env(target, "auditor_url_env", environ),
        "evidence_writer": _database_url_from_env(target, "evidence_writer_url_env", environ),
        "memory_curator": _database_url_from_env(target, "memory_curator_url_env", environ),
    }
    roles = {key: str(target["roles"][key]) for key in ROLE_KEYS}
    secrets = tuple(value for value in (writer_url, reader_url, *role_urls.values()) if value)
    checks: Dict[str, Any] = {}
    endpoints: Dict[str, Mapping[str, Any]] = {}
    for label, url in (("writer", writer_url), ("reader", reader_url)):
        if not url:
            checks[label] = {
                "connection": _check(
                    "SKIP",
                    "%s endpoint URL was not supplied" % label,
                    required=label == "writer" or bool(target.get("require_read_endpoint", False)),
                )
            }
            continue
        try:
            endpoint = probe.inspect_endpoint(url, label)
            endpoints[label] = endpoint
            checks[label] = _endpoint_checks(endpoint, target, label=label)
        except Exception as error:  # noqa: BLE001 - report a redacted live failure
            checks[label] = {
                "connection": _check(
                    "ERROR",
                    _redact_error(error, secrets),
                    required=label == "writer" or bool(target.get("require_read_endpoint", False)),
                    evidence={"location": _safe_location(url)},
                )
            }
    writer = endpoints.get("writer")
    if writer is not None:
        try:
            catalog = probe.inspect_control_plane(writer_url, roles)
            table_map = {str(row["table_name"]): row for row in catalog["tables"]}
            missing_tables = sorted(set(EXPECTED_TABLES) - set(table_map))
            rls_missing = sorted(
                table
                for table in EXPECTED_RLS_TABLES
                if not table_map.get(table, {}).get("relrowsecurity")
            )
            force_missing = sorted(
                table
                for table in EXPECTED_RLS_TABLES
                if not table_map.get(table, {}).get("relforcerowsecurity")
            )
            trigger_map = {str(row["trigger_name"]): row for row in catalog["triggers"]}
            missing_triggers = sorted(set(EXPECTED_TRIGGERS) - set(trigger_map))
            invalid_triggers = sorted(
                trigger
                for trigger, function in EXPECTED_TRIGGER_FUNCTIONS.items()
                if trigger in trigger_map
                and (
                    str(trigger_map[trigger].get("trigger_enabled")) not in {"O", "A"}
                    or str(trigger_map[trigger].get("function_name")) != function
                )
            )
            policy_map = {
                (str(row["tablename"]), str(row["policyname"])): row for row in catalog["policies"]
            }
            invalid_policies: List[str] = []
            for table, policy in zip(EXPECTED_RLS_TABLES, EXPECTED_POLICIES):
                row = policy_map.get((table, policy))
                if row is None:
                    invalid_policies.append("%s.%s missing" % (table, policy))
                    continue
                predicate = "%s %s" % (row.get("qual") or "", row.get("with_check") or "")
                if (
                    str(row.get("cmd", "")).upper() != "ALL"
                    or predicate.count("egoagentos_current_tenant") < 2
                ):
                    invalid_policies.append("%s.%s predicate mismatch" % (table, policy))
            packaged_migrations = _packaged_migrations()
            observed_migrations = {
                str(row["version"]): str(row["sha256"]) for row in catalog["migrations"]
            }
            missing_migrations = sorted(set(packaged_migrations) - set(observed_migrations))
            unexpected_migrations = sorted(set(observed_migrations) - set(packaged_migrations))
            mismatched_migrations = sorted(
                version
                for version in set(packaged_migrations) & set(observed_migrations)
                if packaged_migrations[version] != observed_migrations[version]
            )
            role_result = _role_matrix_ok(catalog["privileges"], roles)
            checks["control_plane"] = {
                "schema": _check(
                    "PASS"
                    if not missing_tables
                    and not missing_migrations
                    and not unexpected_migrations
                    and not mismatched_migrations
                    else "FAIL",
                    "control-plane tables and packaged migration checksums match exactly",
                    required=True,
                    evidence={
                        "missing_tables": missing_tables,
                        "missing_migrations": missing_migrations,
                        "unexpected_migrations": unexpected_migrations,
                        "mismatched_migrations": mismatched_migrations,
                        "observed_migrations": catalog["migrations"],
                    },
                ),
                "rls": _check(
                    "PASS" if not rls_missing and not invalid_policies else "FAIL",
                    "tenant tables have row-level security and the expected tenant policy",
                    required=bool(target.get("require_rls", True)),
                    evidence={
                        "rls_missing": rls_missing,
                        "invalid_policies": invalid_policies,
                    },
                ),
                "force_rls": _check(
                    "PASS"
                    if not force_missing
                    else ("FAIL" if target.get("require_force_rls") else "WARN"),
                    "FORCE ROW LEVEL SECURITY protects against table-owner bypass",
                    required=bool(target.get("require_force_rls", False)),
                    evidence={"force_rls_missing": force_missing},
                ),
                "audit_triggers": _check(
                    "PASS" if not missing_triggers and not invalid_triggers else "FAIL",
                    "append-only, mutation rejection, and notification triggers are enabled and call the expected functions",
                    required=True,
                    evidence={
                        "missing_triggers": missing_triggers,
                        "invalid_triggers": invalid_triggers,
                        "observed": catalog["triggers"],
                    },
                ),
                "role_privileges": _check(
                    "PASS" if role_result["ok"] else "FAIL",
                    "runtime and specialist table/column privileges match the least-privilege matrix",
                    required=True,
                    evidence=role_result,
                ),
            }
        except Exception as error:  # noqa: BLE001
            checks["control_plane"] = {
                "catalog": _check("ERROR", _redact_error(error, secrets), required=True)
            }
    else:
        checks["control_plane"] = {
            "catalog": _check("SKIP", "writer connection failed", required=True)
        }

    for key in ROLE_KEYS:
        label = "%s_login" % key
        url = role_urls[key]
        expected_role = roles[key]
        if not url:
            checks[label] = _check(
                "SKIP",
                "dedicated login URL not supplied; catalog grants are not an authentication proof",
                required=bool(target.get("require_role_logins", False)),
            )
            continue
        try:
            login = probe.inspect_login(url, expected_role)
            ok = (
                login["login_can_login"]
                and login["dedicated_login"]
                and login["capability_group"] == expected_role
                and login["capability_group_nologin"]
                and login["capability_group_member"]
                and login["database_name"] == target["expected_database"]
                and (login["tls"] or not bool(target.get("require_tls", True)))
            )
            checks[label] = _check(
                "PASS" if ok else "FAIL",
                "dedicated LOGIN must be a member of the manifest NOLOGIN capability group and match database/TLS requirements",
                required=bool(target.get("require_role_logins", False)),
                evidence={"expected_role": expected_role, **login},
            )
        except Exception as error:  # noqa: BLE001
            checks[label] = _check(
                "ERROR",
                _redact_error(error, secrets),
                required=bool(target.get("require_role_logins", False)),
            )

    vector_mode = str(target.get("pgvector", "optional"))
    vector = writer.get("pgvector") if writer else None
    if vector_mode == "disabled":
        checks["pgvector"] = _check("SKIP", "pgvector was disabled in the manifest", required=False)
    elif vector is None:
        checks["pgvector"] = _check(
            "FAIL" if vector_mode == "required" else "SKIP",
            "pgvector is not available; it remains an optional future capability",
            required=vector_mode == "required",
        )
    else:
        checks["pgvector"] = _check(
            "PASS"
            if vector.get("extversion")
            else ("FAIL" if vector_mode == "required" else "WARN"),
            "pgvector availability is not the same as an installed, indexed query path",
            required=vector_mode == "required",
            evidence=vector,
        )

    if active_notify:
        try:
            result = probe.active_notify(writer_url)
            checks["active_notify"] = _check(
                "PASS" if result["matched"] else "FAIL",
                "explicit transient LISTEN/NOTIFY round trip",
                required=True,
                evidence=result,
            )
        except Exception as error:  # noqa: BLE001
            checks["active_notify"] = _check("ERROR", _redact_error(error, secrets), required=True)
    else:
        checks["active_notify"] = _check(
            "SKIP",
            "not emitted in default read-only mode; pass --active-notify to send one transient notification",
            required=False,
        )

    if active_topology:
        topology: Dict[str, Any] = {}
        for label, url in (("writer", writer_url), ("reader", reader_url)):
            if not url:
                continue
            try:
                result = probe.active_topology(url)
                expected_accept = label == "writer"
                ok = bool(result["temporary_write_accepted"]) == expected_accept
                topology[label] = _check(
                    "PASS" if ok else "FAIL",
                    "rolled-back TEMP write distinguishes writer from reader",
                    required=label == "writer" or bool(target.get("require_read_endpoint", False)),
                    evidence=result,
                )
            except Exception as error:  # noqa: BLE001
                topology[label] = _check(
                    "ERROR",
                    _redact_error(error, secrets),
                    required=label == "writer" or bool(target.get("require_read_endpoint", False)),
                )
        checks["active_topology"] = topology
    else:
        checks["active_topology"] = _check(
            "SKIP",
            "default mode performs no write, even a rolled-back TEMP write; pass --active-topology explicitly",
            required=False,
        )

    summary = _summarize(checks)
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at": _utc_now(),
        "run_id": "polardb_preflight_%s" % uuid.uuid4().hex,
        "mode": "read_only"
        if not active_notify and not active_topology
        else "explicit_transient_probe",
        "manifest_sha256": _sha256(manifest),
        "target": {
            "environment": environment,
            "expected_database": target["expected_database"],
            "writer_location": _safe_location(writer_url),
            "reader_location": _safe_location(reader_url) if reader_url else None,
        },
        "checks": checks,
        "summary": summary,
        "truth_boundary": {
            "local_postgresql_proof": "separate dated evidence; not upgraded by this report",
            "polardb_preflight": summary["status"],
            "managed_backup": "NOT_RUN",
            "pitr_restore": "NOT_RUN",
            "multi_az_failover": "NOT_RUN",
            "read_write_split_in_api": "NOT_IMPLEMENTED; this CLI only inspects endpoints",
            "cloud_resources_created": False,
        },
    }


def verify_destructive_gate(
    operation: str,
    manifest: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    allow_destructive: bool,
    confirm_database: Optional[str],
    confirm_marker: Optional[str],
) -> Dict[str, Any]:
    if operation not in DESTRUCTIVE_OPERATIONS:
        raise SafetyGateError("unknown destructive acceptance operation")
    manifest_target = manifest["target"]
    operation_manifest = manifest["operations"].get(operation, {})
    if manifest_target["environment"] not in {"nonproduction", "staging"}:
        raise SafetyGateError("destructive acceptance is forbidden for production manifests")
    if not allow_destructive:
        raise SafetyGateError("--allow-destructive is required")
    if (
        not isinstance(operation_manifest, Mapping)
        or operation_manifest.get("authorized") is not True
    ):
        raise SafetyGateError("manifest operations.%s.authorized must be true" % operation)
    expected_database = str(manifest_target["expected_database"])
    observed_database = str(target.get("database_name", ""))
    prefix = str(manifest_target.get("disposable_database_prefix", DEFAULT_DATABASE_PREFIX))
    marker = str(manifest_target.get("disposable_database_marker", DISPOSABLE_MARKER))
    observed_marker = target.get("database_comment")
    if not SAFE_PREFIX.fullmatch(prefix) or not expected_database.startswith(prefix):
        raise SafetyGateError("manifest does not define a safe disposable database prefix")
    if marker != DISPOSABLE_MARKER:
        raise SafetyGateError("manifest does not use the fixed disposable database marker")
    if bool(manifest_target.get("require_tls", True)) and target.get("tls") is not True:
        raise SafetyGateError("destructive acceptance requires a verified TLS session")
    if not observed_database.startswith(prefix):
        raise SafetyGateError("database name does not carry the disposable prefix")
    if observed_database != expected_database or confirm_database != expected_database:
        raise SafetyGateError("manifest, live database, and --confirm-database must match exactly")
    if observed_marker != marker or confirm_marker != marker:
        raise SafetyGateError(
            "database COMMENT, manifest marker, and --confirm-marker must match exactly"
        )
    return {
        "operation": operation,
        "environment": manifest_target["environment"],
        "database_name": observed_database,
        "database_comment": observed_marker,
        "disposable_prefix": prefix,
        "authorized": True,
    }


def run_fresh_schema_replay(
    manifest: Mapping[str, Any],
    environ: Mapping[str, str],
    *,
    allow_destructive: bool,
    confirm_database: Optional[str],
    confirm_marker: Optional[str],
    inspector: Optional[PostgresInspector] = None,
) -> Dict[str, Any]:
    probe = inspector or PostgresInspector()
    writer_url = _database_url_from_env(manifest["target"], "writer_url_env", environ)
    if not writer_url:
        raise ManifestError("writer URL environment variable is unset")
    live_target = probe.disposable_target(writer_url)
    gate = verify_destructive_gate(
        "fresh_schema_replay",
        manifest,
        live_target,
        allow_destructive=allow_destructive,
        confirm_database=confirm_database,
        confirm_marker=confirm_marker,
    )
    # Re-read the marker immediately before the destructive call to narrow target drift.
    repeated_target = probe.disposable_target(writer_url)
    verify_destructive_gate(
        "fresh_schema_replay",
        manifest,
        repeated_target,
        allow_destructive=allow_destructive,
        confirm_database=confirm_database,
        confirm_marker=confirm_marker,
    )
    replay = probe.fresh_schema_replay(
        writer_url,
        expected_database=str(manifest["target"]["expected_database"]),
        expected_marker=DISPOSABLE_MARKER,
        require_tls=bool(manifest["target"].get("require_tls", True)),
    )
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at": _utc_now(),
        "mode": "DESTRUCTIVE_FRESH_SCHEMA_REPLAY",
        "summary": {"status": "PASS", "required_failures": 0},
        "safety_gate": gate,
        "result": replay,
        "truth_boundary": {
            "pitr_restore": "NOT_RUN",
            "cloud_resources_created": False,
            "security_roles_reapply_required": True,
        },
    }


def _write_json(value: Mapping[str, Any], output: str) -> None:
    encoded = (
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    )
    if output == "-":
        sys.stdout.write(encoded)
        return
    Path(output).write_text(encoded, encoding="utf-8")


def _error_report(error: BaseException, secrets: Iterable[str] = ()) -> Dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA,
        "generated_at": _utc_now(),
        "summary": {"status": "ERROR", "required_failures": 1},
        "error": {"type": type(error).__name__, "message": _redact_error(error, secrets)},
        "truth_boundary": {"cloud_resources_created": False, "pitr_restore": "NOT_RUN"},
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ego-polardb-preflight",
        description="Read-only-by-default PostgreSQL/PolarDB live acceptance probe",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate-manifest", help="validate JSON without connecting")
    validate.add_argument("--manifest", type=Path, required=True)
    validate.add_argument("--output", default="-")

    preflight = subparsers.add_parser("preflight", help="run read-only catalog checks")
    preflight.add_argument("--manifest", type=Path, required=True)
    preflight.add_argument("--output", default="-")
    preflight.add_argument(
        "--active-notify",
        action="store_true",
        help="explicitly emit one transient NOTIFY; no durable row is written",
    )
    preflight.add_argument(
        "--active-topology",
        action="store_true",
        help="explicitly attempt a TEMP write on each endpoint and roll it back",
    )
    preflight.add_argument("--allow-production-readonly", action="store_true")

    replay = subparsers.add_parser(
        "fresh-schema-replay",
        help="DESTRUCTIVE: drop/recreate public only after all disposable target gates",
    )
    replay.add_argument("--manifest", type=Path, required=True)
    replay.add_argument("--output", default="-")
    replay.add_argument("--allow-destructive", action="store_true")
    replay.add_argument("--confirm-database")
    replay.add_argument("--confirm-marker")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    secrets: List[str] = []
    try:
        manifest = load_manifest(args.manifest)
        for key in (
            "writer_url_env",
            "reader_url_env",
            "runtime_url_env",
            "auditor_url_env",
            "evidence_writer_url_env",
            "memory_curator_url_env",
        ):
            env_name = manifest["target"].get(key)
            if env_name and os.environ.get(str(env_name)):
                secrets.append(os.environ[str(env_name)])
        if args.command == "validate-manifest":
            report = {
                "schema_version": REPORT_SCHEMA,
                "generated_at": _utc_now(),
                "summary": {"status": "PASS", "required_failures": 0},
                "manifest_sha256": _sha256(manifest),
                "truth_boundary": {"database_connected": False, "cloud_resources_created": False},
            }
        elif args.command == "preflight":
            report = run_preflight(
                manifest,
                os.environ,
                active_notify=args.active_notify,
                active_topology=args.active_topology,
                allow_production_readonly=args.allow_production_readonly,
            )
        else:
            report = run_fresh_schema_replay(
                manifest,
                os.environ,
                allow_destructive=args.allow_destructive,
                confirm_database=args.confirm_database,
                confirm_marker=args.confirm_marker,
            )
        _write_json(report, args.output)
        summary = report.get("summary")
        return 3 if isinstance(summary, Mapping) and summary.get("status") == "FAIL" else 0
    except Exception as error:  # noqa: BLE001 - CLI must always emit redacted machine JSON
        report = _error_report(error, secrets)
        _write_json(report, getattr(args, "output", "-"))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
