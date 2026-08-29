"""Select a persistence backend without leaking database credentials into responses."""

import os
from typing import Optional

from .postgres_store import PostgresStore
from .store import SQLiteStore
from .store_contract import ResearchStore


def create_store(
    *, database_url: Optional[str] = None, sqlite_path: Optional[str] = None
) -> ResearchStore:
    """Create PostgreSQL when configured, otherwise retain local SQLite behavior.

    Explicit arguments take precedence over environment variables. Passing an explicit
    ``sqlite_path`` intentionally bypasses ``EGO_DATABASE_URL`` so unit tests and local
    callers remain deterministic.
    """

    if database_url is not None:
        return PostgresStore(database_url)
    if sqlite_path is not None:
        return SQLiteStore(sqlite_path)

    configured_url = os.getenv("EGO_DATABASE_URL", "").strip()
    if configured_url:
        if configured_url.startswith(("postgresql://", "postgres://")):
            return PostgresStore(configured_url)
        if configured_url.startswith("sqlite:///"):
            return SQLiteStore(configured_url[len("sqlite:///") :])
        raise ValueError("EGO_DATABASE_URL must use postgresql://, postgres://, or sqlite:///")

    return SQLiteStore(os.getenv("EGO_DB_PATH", "/tmp/egoagentos-researchops.sqlite3"))
