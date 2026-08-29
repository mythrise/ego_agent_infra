from pathlib import Path
from urllib.parse import urlsplit

import psycopg
import pytest
from psycopg import sql

from apps.api.polardb_preflight import (
    DISPOSABLE_MARKER,
    run_fresh_schema_replay,
    run_preflight,
)
from apps.api.postgres_store import PostgresStore


ROOT = Path(__file__).resolve().parents[2]


def _database_name(postgres_url: str) -> str:
    return urlsplit(postgres_url).path.lstrip("/")


def _manifest(postgres_url: str, *, destructive: bool = False) -> dict:
    database_name = _database_name(postgres_url)
    return {
        "schema_version": "egoagentos.polardb-live-acceptance/v1",
        "target": {
            "environment": "nonproduction",
            "expected_database": database_name,
            "writer_url_env": "EGO_PREFLIGHT_INTEGRATION_URL",
            "reader_url_env": None,
            "runtime_url_env": None,
            "auditor_url_env": None,
            "evidence_writer_url_env": None,
            "memory_curator_url_env": None,
            "minimum_server_version_num": 120000,
            "require_tls": False,
            "require_polardb_marker": False,
            "require_read_endpoint": False,
            "require_rls": True,
            "require_force_rls": True,
            "require_role_logins": False,
            "disposable_database_prefix": "egoagentos_",
            "disposable_database_marker": DISPOSABLE_MARKER,
            "roles": {
                "runtime": "egoagentos_runtime",
                "auditor": "egoagentos_auditor",
                "evidence_writer": "egoagentos_evidence_writer",
                "memory_curator": "egoagentos_memory_curator",
            },
            "pgvector": "optional",
        },
        "operations": {
            "fresh_schema_replay": {
                "authorized": destructive,
                "status": "NOT_RUN",
            },
            "pitr_restore": {"authorized": False, "status": "NOT_RUN"},
            "multi_az_failover": {"authorized": False, "status": "NOT_RUN"},
        },
        "truth_boundary": {},
    }


def _mark_disposable(postgres_url: str) -> None:
    database_name = _database_name(postgres_url)
    if not database_name.startswith("egoagentos_"):
        pytest.skip("preflight destructive proof requires an egoagentos_ disposable database")
    with psycopg.connect(postgres_url, autocommit=True) as connection:
        # Database names cannot be parameters in DDL. psycopg.sql safely quotes both
        # the derived identifier and the fixed marker literal.
        connection.execute(
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(database_name),
                sql.Literal(DISPOSABLE_MARKER),
            )
        )


def test_real_postgres_preflight_checks_catalog_policies_notify_and_topology(
    postgres_url: str,
) -> None:
    PostgresStore(postgres_url)
    security_sql = (ROOT / "deploy/postgres/security_roles.sql").read_text(encoding="utf-8")
    with psycopg.connect(postgres_url) as connection:
        connection.execute(security_sql)
    _mark_disposable(postgres_url)

    report = run_preflight(
        _manifest(postgres_url),
        {"EGO_PREFLIGHT_INTEGRATION_URL": postgres_url},
        active_notify=True,
        active_topology=True,
    )

    assert report["summary"]["required_failures"] == 0
    assert report["checks"]["writer"]["polardb_identity"]["status"] == "WARN"
    assert report["checks"]["control_plane"]["schema"]["status"] == "PASS"
    assert report["checks"]["control_plane"]["rls"]["status"] == "PASS"
    assert report["checks"]["control_plane"]["force_rls"]["status"] == "PASS"
    assert report["checks"]["control_plane"]["audit_triggers"]["status"] == "PASS"
    assert report["checks"]["control_plane"]["role_privileges"]["status"] == "PASS"
    assert report["checks"]["control_plane"]["role_privileges"]["evidence"][
        "runtime_update_columns"
    ] == {
        "approvals": ["expires_at", "record_json", "status", "token_hash"],
        "audit_events": [],
        "evidence": [],
        "idempotency": [],
        "memory_candidates": [],
        "memories": [],
        "schema_migrations": [],
        "tasks": ["created_at", "generation", "task_json", "updated_at", "version"],
    }
    assert report["checks"]["active_notify"]["status"] == "PASS"
    assert report["checks"]["active_topology"]["writer"]["status"] == "PASS"
    assert report["truth_boundary"]["pitr_restore"] == "NOT_RUN"


def test_real_postgres_fresh_schema_replay_requires_and_preserves_all_gates(
    postgres_url: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    PostgresStore(postgres_url)
    _mark_disposable(postgres_url)
    database_name = _database_name(postgres_url)
    # Fresh replay must explicitly re-apply migrations even when a deployment's
    # normal startup policy is verify-only.
    monkeypatch.setenv("EGO_DATABASE_MIGRATION_MODE", "verify")

    report = run_fresh_schema_replay(
        _manifest(postgres_url, destructive=True),
        {"EGO_PREFLIGHT_INTEGRATION_URL": postgres_url},
        allow_destructive=True,
        confirm_database=database_name,
        confirm_marker=DISPOSABLE_MARKER,
    )

    assert report["summary"]["status"] == "PASS"
    assert report["safety_gate"]["database_name"] == database_name
    assert report["result"]["security_roles_reapply_required"] is True
    assert [row["version"] for row in report["result"]["migrations"]] == [
        "001_control_plane.sql",
        "002_ledger_boundaries.sql",
    ]
    assert "audit_events" in report["result"]["tables"]
    assert report["truth_boundary"]["pitr_restore"] == "NOT_RUN"
