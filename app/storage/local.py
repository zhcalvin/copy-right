"""Local project storage under ``data/projects/<id>/``."""

from pathlib import Path

from app.core.config import get_settings

PROJECT_SUBDIRS = ("source", "clips", "frames", "assets", "results")


def project_dir(project_id: str) -> Path:
    return get_settings().data_dir / "projects" / project_id


def ensure_project_dirs(project_id: str) -> Path:
    root = project_dir(project_id)
    for sub in PROJECT_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    return root
