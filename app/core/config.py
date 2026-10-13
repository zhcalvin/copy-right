"""Runtime settings, read once from environment variables."""

from dataclasses import dataclass
from functools import lru_cache
from os import environ
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    database_url: str
    worker_poll_interval: float
    worker_enabled: bool
    keyring_enabled: bool


def _bool_env(name: str, default: bool) -> bool:
    raw = environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache
def get_settings() -> Settings:
    data_dir = Path(environ.get("COPYRIGHT_DATA_DIR", PROJECT_ROOT / "data")).resolve()
    return Settings(
        data_dir=data_dir,
        database_url=environ.get(
            "COPYRIGHT_DATABASE_URL", f"sqlite:///{data_dir / 'copyright.db'}"
        ),
        worker_poll_interval=float(environ.get("COPYRIGHT_WORKER_POLL_INTERVAL", "1.0")),
        worker_enabled=_bool_env("COPYRIGHT_WORKER_ENABLED", True),
        keyring_enabled=_bool_env("COPYRIGHT_KEYRING", True),
    )
