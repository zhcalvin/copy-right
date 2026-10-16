"""Reference images for characters and scenes (docs/PLAN.md §8 "元素面板").

A reference image is either uploaded or generated with ``image_edit``. Generated images
are candidates: they land in ``assets/generated/`` and are only attached when the user
picks one. Attaching or removing an image saves a new storyboard version, and every shot
showing that character or scene is re-marked (usually as ``swap``).
"""

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.capabilities.base import Capability
from app.models import Job, JobStatus, StoryboardSource
from app.pipeline.artifacts import ArtifactMissing, save_artifact, suffix_for
from app.pipeline.jobs import enqueue_job, update_job
from app.providers.settings import CapabilityUnavailable, selection_for
from app.storage.local import project_dir
from app.storyboard.schema import StoryboardDoc
from app.storyboard.versions import VersionError, latest_doc, save_version

logger = logging.getLogger(__name__)

PURPOSE = "asset_image"
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_REFERENCES = 4
GENERATE_SUFFIX = " Full body, facing the camera, plain light background, no text."


class AssetError(Exception):
    def __init__(self, message: str, *, error_type: str = "input_invalid") -> None:
        super().__init__(message)
        self.error_type = error_type


def _find(doc: StoryboardDoc, asset_id: str) -> tuple[str, Any]:
    for character in doc.characters:
        if character.character_id == asset_id:
            return "characters", character
    for scene in doc.scenes:
        if scene.scene_id == asset_id:
            return "scenes", scene
    raise AssetError(f"No character or scene {asset_id}", error_type="not_found")


def _with_images(doc: StoryboardDoc, asset_id: str, images: list[str]) -> StoryboardDoc:
    field, _ = _find(doc, asset_id)
    items = [
        item.model_copy(update={"reference_images": images})
        if getattr(item, "character_id", getattr(item, "scene_id", None)) == asset_id
        else item
        for item in getattr(doc, field)
    ]
    return doc.model_copy(update={field: items})


def _save(session: Session, project_id: str, doc: StoryboardDoc, note: str) -> StoryboardDoc:
    saved, _ = save_version(session, project_id, doc, source=StoryboardSource.USER_EDIT, note=note)
    return saved


def attach_image(session: Session, project_id: str, asset_id: str, relative: str) -> StoryboardDoc:
    """Make ``relative`` (a path inside the project) the asset's main reference image."""
    try:
        doc = latest_doc(session, project_id)
    except VersionError as exc:
        raise AssetError(str(exc), error_type=exc.error_type) from exc
    _, item = _find(doc, asset_id)
    images = [relative, *(p for p in item.reference_images if p != relative)][:MAX_REFERENCES]
    return _save(
        session, project_id, _with_images(doc, asset_id, images), f"New look for {asset_id}"
    )


def detach_image(session: Session, project_id: str, asset_id: str, relative: str) -> StoryboardDoc:
    try:
        doc = latest_doc(session, project_id)
    except VersionError as exc:
        raise AssetError(str(exc), error_type=exc.error_type) from exc
    _, item = _find(doc, asset_id)
    if relative not in item.reference_images:
        raise AssetError("That image is not attached", error_type="not_found")
    images = [p for p in item.reference_images if p != relative]
    return _save(
        session, project_id, _with_images(doc, asset_id, images), f"Removed an image of {asset_id}"
    )


def store_upload(project_id: str, asset_id: str, filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise AssetError(f"Use one of {', '.join(sorted(IMAGE_SUFFIXES))}")
    if len(content) > MAX_IMAGE_BYTES:
        raise AssetError("The image is larger than 20 MB")
    folder = project_dir(project_id) / "assets"
    folder.mkdir(parents=True, exist_ok=True)
    index = len(list(folder.glob(f"{asset_id}_*")))
    target = folder / f"{asset_id}_{index + 1}{suffix}"
    target.write_bytes(content)
    return target.relative_to(project_dir(project_id)).as_posix()


# ---------------------------------------------------------------------------
# Generation with image_edit
# ---------------------------------------------------------------------------


class GenerateOptions(BaseModel):
    prompt: str = Field(min_length=1, max_length=1500)
    # Project-relative images to start from; default: the asset's current reference
    # image, or a keyframe of the first shot it appears in.
    images: list[str] = Field(default_factory=list, max_length=3)


def _default_sources(doc: StoryboardDoc, asset_id: str, item: Any) -> list[str]:
    if item.reference_images:
        return item.reference_images[:1]
    for shot in doc.shots:
        shown = asset_id in shot.assets.character_ids or shot.assets.scene_id == asset_id
        if shown and shot.source and shot.source.keyframes:
            keyframes = shot.source.keyframes
            return [keyframes[len(keyframes) // 2]]
    return []


def start_generation(
    session: Session, project_id: str, asset_id: str, options: GenerateOptions
) -> Job:
    try:
        doc = latest_doc(session, project_id)
        selection = selection_for(session, Capability.IMAGE_EDIT)
    except VersionError as exc:
        raise AssetError(str(exc), error_type=exc.error_type) from exc
    except CapabilityUnavailable as exc:
        raise AssetError(str(exc), error_type="not_configured") from exc
    _, item = _find(doc, asset_id)
    root = project_dir(project_id).resolve()
    sources = options.images or _default_sources(doc, asset_id, item)
    paths = []
    for relative in sources:
        path = (root / relative).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise AssetError(f"Image not found: {relative}", error_type="not_found")
        paths.append(str(path))
    if not paths:
        raise AssetError("Upload an image first; there is nothing to start from")
    return enqueue_job(
        session,
        capability=Capability.IMAGE_EDIT,
        provider=selection.provider or "",
        model=selection.model,
        project_id=project_id,
        purpose=PURPOSE,
        request={
            "images": paths,
            "prompt": options.prompt.strip() + GENERATE_SUFFIX,
            "asset_id": asset_id,
            "user_prompt": options.prompt.strip(),
        },
    )


class Candidate(BaseModel):
    job_id: str
    asset_id: str
    status: JobStatus
    prompt: str
    image: str | None = None  # project-relative, once downloaded
    error_type: str | None = None
    error: str | None = None


def candidates(session: Session, project_id: str) -> list[Candidate]:
    jobs = session.exec(
        select(Job)
        .where(Job.project_id == project_id, Job.purpose == PURPOSE)
        .order_by(Job.created_at.desc())
    ).all()
    return [
        Candidate(
            job_id=job.id,
            asset_id=str(job.request.get("asset_id", "")),
            status=job.status,
            prompt=str(job.request.get("user_prompt", "")),
            image=(job.result or {}).get("local_path"),
            error_type=job.error_type,
            error=job.error,
        )
        for job in jobs
    ]


def candidate_image(session: Session, project_id: str, job_id: str) -> tuple[str, str]:
    job = session.get(Job, job_id)
    if job is None or job.project_id != project_id or job.purpose != PURPOSE:
        raise AssetError("No such generated image", error_type="not_found")
    image = (job.result or {}).get("local_path")
    if job.status != JobStatus.SUCCEEDED or not image:
        raise AssetError("This image is not ready", error_type="conflict")
    return str(job.request.get("asset_id", "")), image


async def on_job_finished(session: Session, job: Job) -> None:
    """Worker hook: download a generated reference image into the project."""
    if job.purpose != PURPOSE or job.project_id is None or job.status != JobStatus.SUCCEEDED:
        return
    root = project_dir(job.project_id)
    target = root / "assets" / "generated" / f"{job.id}{suffix_for(job.result, '.png')}"
    try:
        await save_artifact(job.result, target)
    except ArtifactMissing as exc:
        logger.warning("asset job %s: %s", job.id, exc)
        update_job(
            session, job, status=JobStatus.FAILED, error_type="provider_error", error=str(exc)
        )
        return
    update_job(
        session,
        job,
        result={**(job.result or {}), "local_path": target.relative_to(root).as_posix()},
    )
