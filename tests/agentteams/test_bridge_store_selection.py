import re
from pathlib import Path

import pytest

from apps.agentteams_bridge.settings import BridgeSettings
from apps.agentteams_bridge.store import BridgeStore, build_bridge_store


ROOT = Path(__file__).resolve().parents[2]


def test_dedicated_database_url_is_explicit_and_redacted_from_settings_repr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "postgresql://bridge:do-not-print@postgres/egoagentos"
    migration_url = "postgresql://owner:also-secret@postgres/egoagentos"
    monkeypatch.setenv("EGO_AGENTTEAMS_DATABASE_URL", url)
    monkeypatch.setenv("EGO_AGENTTEAMS_MIGRATION_DATABASE_URL", migration_url)
    settings = BridgeSettings.from_env()
    assert settings.database_url == url
    assert settings.migration_database_url == migration_url
    assert "do-not-print" not in repr(settings)
    assert "also-secret" not in repr(settings)


def test_blank_database_url_uses_sqlite_development_fallback() -> None:
    store = build_bridge_store(database_url="", sqlite_path=":memory:")
    assert isinstance(store, BridgeStore)
    assert store.engine == "sqlite"


def test_explicit_non_postgres_url_fails_instead_of_falling_back() -> None:
    with pytest.raises(ValueError, match="must use postgresql"):
        build_bridge_store(database_url="sqlite:///not-allowed.sqlite3", sqlite_path=":memory:")


def test_invalid_bridge_migration_mode_fails_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EGO_AGENTTEAMS_MIGRATION_MODE", "invalid")
    with pytest.raises(ValueError, match="must be apply or verify"):
        build_bridge_store(database_url="postgresql://runtime:secret@127.0.0.1/unused")


def test_campaign_authority_migration_is_append_only_and_contains_no_bearer_columns() -> None:
    migration = (
        ROOT
        / "apps/agentteams_bridge/migrations/postgres/002_campaign_safety_attention.sql"
    ).read_text(encoding="utf-8")

    assert "ALTER TABLE bridge_runs" in migration
    assert "CREATE TABLE bridge_extension_events" in migration
    assert "CREATE TABLE bridge_task_leases" in migration
    assert "CREATE TABLE bridge_evaluator_bindings" in migration
    assert "pg_advisory_xact_lock" in migration
    assert "BEFORE UPDATE OR DELETE" in migration
    assert "BEFORE TRUNCATE" in migration
    assert "pg_notify" in migration
    assert "current_setting('egoagentos.tenant_id'" in migration
    assert "current_setting('egoagentos.project_id'" in migration
    for forbidden_column in (
        "approval_bearer",
        "bearer_token",
        "database_url",
        "dsn",
        "hidden_evaluator",
        "private_key",
        "prompt_text",
        "raw_key",
        "secret",
    ):
        assert re.search(
            r"\b%s\s+(?:text|bytea|jsonb)\b" % re.escape(forbidden_column),
            migration.lower(),
        ) is None
