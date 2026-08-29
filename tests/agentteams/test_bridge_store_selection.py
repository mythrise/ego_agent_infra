import pytest

from apps.agentteams_bridge.settings import BridgeSettings
from apps.agentteams_bridge.store import BridgeStore, build_bridge_store


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
