from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.db import get_engine, init_db


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch) -> Iterator[None]:
    """Give every test its own data dir and database."""
    monkeypatch.setenv("COPYRIGHT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("COPYRIGHT_WORKER_ENABLED", "false")
    # Never touch the developer's real keychain from tests.
    monkeypatch.setenv("COPYRIGHT_KEYRING", "false")
    _clear_caches()
    yield
    get_engine().dispose()
    _clear_caches()


def _clear_caches() -> None:
    get_settings.cache_clear()
    get_engine.cache_clear()
    get_cipher.cache_clear()


@pytest.fixture
def engine() -> Engine:
    init_db()
    return get_engine()


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
