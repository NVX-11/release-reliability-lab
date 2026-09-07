"""Shared test fixtures."""

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import task_store


@pytest.fixture(autouse=True)
def reset_task_store() -> Iterator[None]:
    task_store.clear()
    yield
    task_store.clear()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
