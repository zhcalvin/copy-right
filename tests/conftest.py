import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.core.config import get_settings
from app.core.crypto import get_cipher
from app.core.db import get_engine, init_db
from app.tools.media import ffmpeg_available


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


# Three solid-colour shots: red 0–1 s, blue 1–2 s, green 2–3.5 s.
SAMPLE_CUTS = (1.0, 2.0)
SAMPLE_DURATION = 3.5


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    if not ffmpeg_available():
        pytest.skip("ffmpeg is not installed")
    out = tmp_path_factory.mktemp("media") / "three_shots.mp4"
    colours = [("red", 1.0), ("blue", 1.0), ("green", 1.5)]
    inputs = [
        arg
        for colour, seconds in colours
        for arg in ("-f", "lavfi", "-i", f"color=c={colour}:s=160x120:r=30:d={seconds}")
    ]
    filter_graph = "".join(f"[{i}:v]" for i in range(len(colours))) + (
        f"concat=n={len(colours)}:v=1:a=0[v]"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            filter_graph,
            "-map",
            "[v]",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ],
        check=True,
    )
    return out
