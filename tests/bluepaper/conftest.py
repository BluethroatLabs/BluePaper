from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from bluepaper.api.app import create_app
from bluepaper.config import Settings
from bluepaper.storage.base import Stores
from bluepaper.storage.memory import memory_stores


@pytest.fixture
def stores() -> Stores:
    return memory_stores()


@pytest.fixture
def settings() -> Settings:
    return Settings(
        api_key="test-key",
        storage_backend="memory",
        isolation="dummy",
        max_upload_bytes=1024 * 1024,
        max_concurrent_jobs=2,
        max_queue_depth=3,
        turnstile_secret=None,
        turnstile_hostnames="",
    )


@pytest.fixture
def client(settings: Settings, stores: Stores) -> TestClient:
    return TestClient(create_app(settings, stores))


def auth(api_key: str = "test-key") -> dict[str, str]:
    return {"Authorization": f"Bearer {api_key}"}
