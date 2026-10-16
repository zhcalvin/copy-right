"""Storyboard editing: versions, manual edits, reference images and plot edits (M3)."""

from typing import Annotated, Any

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field

from app.core.db import SessionDep
from app.models import Job, Project, StoryboardSource
from app.pipeline import assets, plot_edit
from app.storyboard.versions import (
    VersionError,
    VersionInfo,
    list_versions,
    load,
    restore_version,
    row_for,
    save_version,
)
from app.storyboard.versions import latest_doc as _latest_doc

router = APIRouter(prefix="/projects", tags=["storyboard"])

ERROR_STATUS = {
    "not_found": status.HTTP_404_NOT_FOUND,
    "conflict": status.HTTP_409_CONFLICT,
    "not_configured": status.HTTP_409_CONFLICT,
    "input_invalid": status.HTTP_422_UNPROCESSABLE_CONTENT,
}


def _http(exc: VersionError | assets.AssetError | plot_edit.PlotEditError) -> HTTPException:
    code = ERROR_STATUS.get(exc.error_type, status.HTTP_422_UNPROCESSABLE_CONTENT)
    return HTTPException(code, str(exc))


def _project(session: SessionDep, project_id: str) -> Project:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return project


class SavedStoryboard(BaseModel):
    storyboard: dict[str, Any]
    changed: list[str]


# -- versions ------------------------------------------------------------------------


@router.get("/{project_id}/storyboard")
def get_storyboard(
    project_id: str, session: SessionDep, version: int | None = None
) -> dict[str, Any]:
    _project(session, project_id)
    try:
        if version is None:
            return _latest_doc(session, project_id).model_dump(mode="json")
    except VersionError as exc:
        raise _http(exc) from exc
    row = row_for(session, project_id, version)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Version {version} does not exist")
    return load(row).model_dump(mode="json")


@router.get("/{project_id}/storyboard/versions")
def get_versions(project_id: str, session: SessionDep) -> list[VersionInfo]:
    _project(session, project_id)
    return list_versions(session, project_id)


class StoryboardEdit(BaseModel):
    storyboard: dict[str, Any]
    base_version: int
    note: str | None = Field(default=None, max_length=200)


@router.put("/{project_id}/storyboard")
def put_storyboard(project_id: str, body: StoryboardEdit, session: SessionDep) -> SavedStoryboard:
    _project(session, project_id)
    try:
        doc, changed = save_version(
            session,
            project_id,
            body.storyboard,
            source=StoryboardSource.USER_EDIT,
            note=body.note,
            base_version=body.base_version,
        )
    except VersionError as exc:
        raise _http(exc) from exc
    return SavedStoryboard(storyboard=doc.model_dump(mode="json"), changed=changed)


class RestoreRequest(BaseModel):
    version: int = Field(ge=1)


@router.post("/{project_id}/storyboard/restore")
def restore(project_id: str, body: RestoreRequest, session: SessionDep) -> dict[str, Any]:
    _project(session, project_id)
    try:
        return restore_version(session, project_id, body.version).model_dump(mode="json")
    except VersionError as exc:
        raise _http(exc) from exc


# -- reference images ----------------------------------------------------------------


@router.post("/{project_id}/assets/{asset_id}/images")
async def upload_reference(
    project_id: str, asset_id: str, session: SessionDep, file: Annotated[UploadFile, File()]
) -> dict[str, Any]:
    _project(session, project_id)
    content = await file.read(assets.MAX_IMAGE_BYTES + 1)
    try:
        relative = assets.store_upload(project_id, asset_id, file.filename or "", content)
        doc = assets.attach_image(session, project_id, asset_id, relative)
    except (assets.AssetError, VersionError) as exc:
        raise _http(exc) from exc
    return doc.model_dump(mode="json")


class ImageRef(BaseModel):
    path: str


@router.post("/{project_id}/assets/{asset_id}/images/remove")
def remove_reference(
    project_id: str, asset_id: str, body: ImageRef, session: SessionDep
) -> dict[str, Any]:
    _project(session, project_id)
    try:
        return assets.detach_image(session, project_id, asset_id, body.path).model_dump(mode="json")
    except (assets.AssetError, VersionError) as exc:
        raise _http(exc) from exc


@router.post("/{project_id}/assets/{asset_id}/generate", status_code=status.HTTP_202_ACCEPTED)
def generate_reference(
    project_id: str, asset_id: str, body: assets.GenerateOptions, session: SessionDep
) -> assets.Candidate:
    _project(session, project_id)
    try:
        job = assets.start_generation(session, project_id, asset_id, body)
    except assets.AssetError as exc:
        raise _http(exc) from exc
    return next(c for c in assets.candidates(session, project_id) if c.job_id == job.id)


@router.get("/{project_id}/assets/candidates")
def list_candidates(project_id: str, session: SessionDep) -> list[assets.Candidate]:
    _project(session, project_id)
    return assets.candidates(session, project_id)


class UseCandidate(BaseModel):
    job_id: str


@router.post("/{project_id}/assets/candidates/use")
def use_candidate(project_id: str, body: UseCandidate, session: SessionDep) -> dict[str, Any]:
    _project(session, project_id)
    try:
        asset_id, image = assets.candidate_image(session, project_id, body.job_id)
        return assets.attach_image(session, project_id, asset_id, image).model_dump(mode="json")
    except (assets.AssetError, VersionError) as exc:
        raise _http(exc) from exc


# -- plot edits ----------------------------------------------------------------------


def _plot_job(session: SessionDep, project_id: str, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None or job.project_id != project_id or job.purpose != plot_edit.PURPOSE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Plot edit not found")
    return job


@router.post("/{project_id}/plot-edits", status_code=status.HTTP_202_ACCEPTED)
def create_plot_edit(
    project_id: str, body: plot_edit.PlotEditOptions, session: SessionDep
) -> plot_edit.Proposal:
    _project(session, project_id)
    try:
        job = plot_edit.start_plot_edit(session, project_id, body)
    except plot_edit.PlotEditError as exc:
        raise _http(exc) from exc
    return plot_edit.proposal_for(session, job)


@router.get("/{project_id}/plot-edits/{job_id}")
def get_plot_edit(project_id: str, job_id: str, session: SessionDep) -> plot_edit.Proposal:
    return plot_edit.proposal_for(session, _plot_job(session, project_id, job_id))


class ApplyPlotEdit(BaseModel):
    keys: list[str] = Field(min_length=1)


@router.post("/{project_id}/plot-edits/{job_id}/apply")
def apply_plot_edit(
    project_id: str, job_id: str, body: ApplyPlotEdit, session: SessionDep
) -> SavedStoryboard:
    job = _plot_job(session, project_id, job_id)
    try:
        doc, changed = plot_edit.apply_proposal(session, job, body.keys)
    except (plot_edit.PlotEditError, VersionError) as exc:
        raise _http(exc) from exc
    return SavedStoryboard(storyboard=doc.model_dump(mode="json"), changed=changed)
