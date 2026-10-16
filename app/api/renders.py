"""Generation runs: start, follow, approve first frames, retry shots, cancel (M4)."""

from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.db import SessionDep
from app.models import Project, Render, RenderStatus
from app.pipeline import render as pipeline

router = APIRouter(prefix="/projects", tags=["renders"])

ERROR_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "conflict": status.HTTP_409_CONFLICT,
    "not_configured": status.HTTP_409_CONFLICT,
}


class RenderRead(BaseModel):
    id: str
    project_id: str
    storyboard_version: int
    status: RenderStatus
    quality: str
    approve_frames: bool
    shots: dict[str, Any]
    output: str | None
    error_type: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


def _read(render: Render) -> RenderRead:
    return RenderRead.model_validate(render.model_dump())


def _http(exc: pipeline.RenderError) -> HTTPException:
    return HTTPException(
        ERROR_STATUS.get(exc.error_type, status.HTTP_422_UNPROCESSABLE_CONTENT), str(exc)
    )


def _render(session: SessionDep, project_id: str, render_id: str) -> Render:
    render = session.get(Render, render_id)
    if render is None or render.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Render not found")
    return render


@router.post("/{project_id}/renders", status_code=status.HTTP_202_ACCEPTED)
def create_render(
    project_id: str, session: SessionDep, options: pipeline.RenderOptions | None = None
) -> RenderRead:
    try:
        render = pipeline.start_render(session, project_id, options or pipeline.RenderOptions())
    except pipeline.RenderError as exc:
        raise _http(exc) from exc
    return _read(render)


@router.get("/{project_id}/renders")
def list_renders(project_id: str, session: SessionDep) -> list[RenderRead]:
    if session.get(Project, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    rows = session.exec(
        select(Render).where(Render.project_id == project_id).order_by(Render.created_at.desc())
    ).all()
    return [_read(r) for r in rows]


@router.get("/{project_id}/renders/{render_id}")
def get_render(project_id: str, render_id: str, session: SessionDep) -> RenderRead:
    return _read(_render(session, project_id, render_id))


class FramePrompt(BaseModel):
    prompt: str | None = Field(default=None, max_length=1000)


class RetryOptions(BaseModel):
    provider: str | None = None
    model: str | None = None


@router.post("/{project_id}/renders/{render_id}/shots/{shot_id}/approve")
def approve(project_id: str, render_id: str, shot_id: str, session: SessionDep) -> RenderRead:
    render = _render(session, project_id, render_id)
    try:
        return _read(pipeline.approve_frame(session, render, shot_id))
    except pipeline.RenderError as exc:
        raise _http(exc) from exc


@router.post("/{project_id}/renders/{render_id}/approve-all")
def approve_all(project_id: str, render_id: str, session: SessionDep) -> RenderRead:
    render = _render(session, project_id, render_id)
    waiting = [sid for sid, s in render.shots.items() if s.get("status") == "frame_ready"]
    try:
        for shot_id in waiting:
            pipeline.approve_frame(session, render, shot_id)
    except pipeline.RenderError as exc:
        raise _http(exc) from exc
    return _read(render)


@router.post("/{project_id}/renders/{render_id}/shots/{shot_id}/redo-frame")
def redo_frame(
    project_id: str, render_id: str, shot_id: str, body: FramePrompt, session: SessionDep
) -> RenderRead:
    render = _render(session, project_id, render_id)
    try:
        return _read(pipeline.redo_frame(session, render, shot_id, body.prompt))
    except pipeline.RenderError as exc:
        raise _http(exc) from exc


@router.post("/{project_id}/renders/{render_id}/shots/{shot_id}/retry")
def retry(
    project_id: str, render_id: str, shot_id: str, body: RetryOptions, session: SessionDep
) -> RenderRead:
    render = _render(session, project_id, render_id)
    try:
        return _read(pipeline.retry_shot(session, render, shot_id, body.provider, body.model))
    except pipeline.RenderError as exc:
        raise _http(exc) from exc


@router.post("/{project_id}/renders/{render_id}/cancel")
def cancel(project_id: str, render_id: str, session: SessionDep) -> RenderRead:
    render = _render(session, project_id, render_id)
    try:
        return _read(pipeline.cancel_render(session, render))
    except pipeline.RenderError as exc:
        raise _http(exc) from exc
