import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.db import SessionDep, get_engine
from app.models import Project, ProjectStatus, Storyboard
from app.pipeline.parse import ParseError, ParseOptions, begin_parse, run_parse
from app.storage.local import ensure_project_dirs, project_dir
from app.tools.media import MediaError, probe

router = APIRouter(prefix="/projects", tags=["projects"])

MAX_UPLOAD_BYTES = 1024 * 1024 * 1024
MAX_DURATION_S = 180.0  # docs/PLAN.md: single user, local, videos up to 3 minutes
UPLOAD_CHUNK = 1024 * 1024
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"})

PARSE_ERROR_STATUS = {
    "input_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
    "not_configured": status.HTTP_409_CONFLICT,
    "conflict": status.HTTP_409_CONFLICT,
}


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectRead(BaseModel):
    id: str
    name: str
    status: ProjectStatus
    stage: str | None
    error_type: str | None
    error: str | None
    duration: float | None
    width: int | None
    height: int | None
    fps: float | None
    has_video: bool
    # Relative to the project's files endpoint, e.g. "source/original.mp4".
    source_file: str | None
    created_at: datetime


def _read(project: Project) -> ProjectRead:
    source = Path(project.source_video_path) if project.source_video_path else None
    return ProjectRead.model_validate(
        {
            **project.model_dump(),
            "has_video": source is not None,
            "source_file": f"source/{source.name}" if source else None,
        }
    )


def _get(session: SessionDep, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


@router.get("")
def list_projects(session: SessionDep) -> list[ProjectRead]:
    projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()
    return [_read(p) for p in projects]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, session: SessionDep) -> ProjectRead:
    project = Project(name=body.name.strip())
    if not project.name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Project name cannot be empty")
    session.add(project)
    session.commit()
    session.refresh(project)
    ensure_project_dirs(project.id)
    return _read(project)


@router.get("/{project_id}")
def get_project(project_id: str, session: SessionDep) -> ProjectRead:
    return _read(_get(session, project_id))


async def _save_upload(upload: UploadFile, target: Path) -> None:
    written = 0
    with target.open("wb") as out:
        while chunk := await upload.read(UPLOAD_CHUNK):
            written += len(chunk)
            if written > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status.HTTP_413_CONTENT_TOO_LARGE, "The video is larger than 1 GB"
                )
            out.write(chunk)


@router.post("/{project_id}/video")
async def upload_video(
    project_id: str,
    session: SessionDep,
    file: Annotated[UploadFile, File()],
    rights_confirmed: Annotated[bool, Form()] = False,
) -> ProjectRead:
    """Store the source video. The user must confirm they hold the rights (PLAN §10)."""
    project = _get(session, project_id)
    if not rights_confirmed:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Confirm that you own this footage and the right to use the people in it",
        )
    if project.status == ProjectStatus.PARSING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Wait for parsing to finish")
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in VIDEO_SUFFIXES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Unsupported file type; use one of {', '.join(sorted(VIDEO_SUFFIXES))}",
        )
    source_dir = ensure_project_dirs(project.id) / "source"
    target = source_dir / f"original{suffix}"
    partial = source_dir / f"upload{suffix}.part"
    try:
        await _save_upload(file, partial)
        info = await asyncio.to_thread(probe, partial)
    except MediaError as exc:
        partial.unlink(missing_ok=True)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Not a readable video: {exc}"
        ) from exc
    except HTTPException:
        partial.unlink(missing_ok=True)
        raise
    if info.duration > MAX_DURATION_S:
        partial.unlink(missing_ok=True)
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"The video is {info.duration:.0f}s long; the limit is {MAX_DURATION_S:.0f}s",
        )
    for old in source_dir.glob("original.*"):
        old.unlink()
    partial.rename(target)
    project.sqlmodel_update(
        {
            "source_video_path": str(target),
            "duration": info.duration,
            "width": info.width,
            "height": info.height,
            "fps": info.fps,
            "status": ProjectStatus.CREATED,
            "stage": None,
            "error_type": None,
            "error": None,
        }
    )
    session.add(project)
    session.commit()
    session.refresh(project)
    return _read(project)


@router.post("/{project_id}/parse", status_code=status.HTTP_202_ACCEPTED)
def parse_project(
    project_id: str,
    session: SessionDep,
    background: BackgroundTasks,
    options: ParseOptions | None = None,
) -> ProjectRead:
    project = _get(session, project_id)
    try:
        begin_parse(session, project)
    except ParseError as exc:
        code = PARSE_ERROR_STATUS.get(exc.error_type, status.HTTP_422_UNPROCESSABLE_CONTENT)
        raise HTTPException(code, str(exc)) from exc
    background.add_task(run_parse, get_engine(), project.id, options or ParseOptions())
    session.refresh(project)
    return _read(project)


@router.get("/{project_id}/storyboard")
def get_storyboard(project_id: str, session: SessionDep) -> dict[str, Any]:
    _get(session, project_id)
    latest = session.exec(
        select(Storyboard)
        .where(Storyboard.project_id == project_id)
        .order_by(Storyboard.version.desc())
    ).first()
    if latest is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This project has no storyboard yet")
    return latest.content


@router.get("/{project_id}/files/{file_path:path}")
def get_file(project_id: str, file_path: str, session: SessionDep) -> FileResponse:
    """Serve clips, frames and the source video. Paths cannot escape the project dir."""
    _get(session, project_id)
    root = project_dir(project_id).resolve()
    target = (root / file_path).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "File not found")
    return FileResponse(target)
