from __future__ import annotations

import re
from pathlib import Path
from typing import Iterator

import pytest

from apps.api.postgres_store import PostgresStore


MIGRATION = (
    Path(__file__).resolve().parents[2] / "apps/api/migrations/postgres/003_trusted_memory_core.sql"
)
TRUSTED_TABLES = (
    "trusted_memory_streams",
    "trusted_memory_history",
    "trusted_memory_current",
    "trusted_memory_closures",
    "trusted_memory_outbox",
)


@pytest.fixture(autouse=True)
def fresh_public_schema() -> Iterator[None]:
    """Static checks run without claiming a PostgreSQL runtime execution."""

    yield


def _migration_sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def test_trusted_memory_migration_is_tenant_scoped_immutable_and_least_privilege() -> None:
    migration = _migration_sql()
    normalized = " ".join(migration.casefold().split())

    for table in TRUSTED_TABLES:
        table_match = re.search(
            rf"create table if not exists {table}\s*\((.*?)\n\);",
            migration,
            re.IGNORECASE | re.DOTALL,
        )
        assert table_match is not None, table
        assert re.search(
            r"\btenant_id\s+text\s+not\s+null\b",
            table_match.group(1),
            re.IGNORECASE,
        )
        assert f"alter table {table} enable row level security" in normalized
        assert f"alter table {table} force row level security" in normalized
        assert f"create policy {table}_tenant" in normalized

    assert "current_setting('egoagentos.tenant_id', true)" in normalized
    assert "pg_advisory_xact_lock" in normalized
    assert "new.tenant_id" in normalized
    assert "new.project_id" in normalized
    assert "new.lineage_id" in normalized
    assert "before update or delete on trusted_memory_history" in normalized
    assert "before truncate on trusted_memory_history" in normalized
    assert "before update or delete on trusted_memory_closures" in normalized
    assert "before update or delete on trusted_memory_outbox" in normalized
    assert "trusted_memory_current_guard" in normalized
    assert "pg_notify('ego_trusted_memory_events'" in normalized
    assert "after insert on trusted_memory_outbox" in normalized
    assert "egoagentos_memory_writer" in normalized
    assert "egoagentos_memory_reader" in normalized
    assert "grant select" in normalized
    assert "grant insert" in normalized
    assert "grant update" in normalized
    assert not re.search(
        r"\b(?:approval|bearer|capability|token|prompt|secret|raw_key|dsn)\b",
        migration,
        re.IGNORECASE,
    )


def test_postgres_store_exposes_the_complete_trusted_memory_surface() -> None:
    for method in (
        "append_trusted_memory_record",
        "get_trusted_memory_event",
        "list_trusted_memory_history",
        "get_current_trusted_fact",
        "get_trusted_memory_stream_root",
        "verify_trusted_memory_stream",
        "list_legacy_memory_views",
    ):
        assert callable(getattr(PostgresStore, method, None)), method
