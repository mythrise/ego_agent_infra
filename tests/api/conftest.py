from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from apps.api.main import create_app
from tests.api.operator_auth_helpers import (
    TEST_AUTHORIZATION_HEADERS,
    TEST_OPERATOR_ID,
    TEST_OPERATOR_KEY,
)


@pytest.fixture()
def client(tmp_path: Path) -> Iterator[TestClient]:
    application = create_app(
        str(tmp_path / "researchops.sqlite3"),
        operator_key=TEST_OPERATOR_KEY,
        operator_id=TEST_OPERATOR_ID,
    )
    with TestClient(application) as test_client:
        test_client.headers.update(TEST_AUTHORIZATION_HEADERS)
        yield test_client
