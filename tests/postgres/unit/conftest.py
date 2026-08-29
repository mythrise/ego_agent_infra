from typing import Iterator

import pytest


@pytest.fixture(autouse=True)
def fresh_public_schema() -> Iterator[None]:
    """Override the integration fixture for SQL source-contract unit tests."""

    yield
