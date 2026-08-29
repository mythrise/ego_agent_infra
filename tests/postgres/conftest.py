import os
from typing import Iterator

import psycopg
import pytest


@pytest.fixture(scope="session")
def postgres_url() -> str:
    value = os.getenv("EGO_TEST_POSTGRES_URL", "").strip()
    if not value:
        pytest.skip("set EGO_TEST_POSTGRES_URL to run PostgreSQL integration tests")
    return value


@pytest.fixture(autouse=True)
def fresh_public_schema(postgres_url: str) -> Iterator[None]:
    """Give every integration test a fresh schema on the explicit test database."""

    with psycopg.connect(postgres_url, autocommit=True) as connection:
        connection.execute("DROP SCHEMA IF EXISTS public CASCADE")
        connection.execute("CREATE SCHEMA public")
    yield
