"""Describe one shot again (docs/PLAN.md §7 step 3, "按需解析" / "重新解析此镜头").

A new description corrects the storyboard's index of the footage; it is not an edit. So
the result is written twice: into a new parsed baseline (so the shot does not count as
changed) and into the user's latest version (so their other edits stay).
"""

import logging
from pathlib import Path

from pydantic import ValidationError
from sqlmodel import Session

from app.capabilities.base import Capability
from app.capabilities.video_understanding import UnderstandingResult
from app.models import Job, JobStatus, Storyboard, StoryboardSource
from app.pipeline.jobs import enqueue_job
from app.pipeline.parse import PreparedShot, _shot
from app.providers.settings import CapabilityUnavailable, selection_for
from app.storage.local import project_dir
from app.storyboard.schema import Shot, StoryboardDoc
from app.storyboard.versions import (
    VersionError,
    baseline_doc,
    latest_doc,
    next_version,
    save_version,
)

logger = logging.getLogger(__name__)

PURPOSE = "reparse"


class ReparseError(Exception):
    def __init__(self, message: str, *, error_type: str = "input_invalid") -> None:
        super().__init__(message)
        self.error_type = error_type


def _frames(project_id: str, shot: Shot) -> list[str]:
    folder = project_dir(project_id) / "frames"
    frames = sorted((folder / "analysis").glob(f"{shot.shot_id}_*.jpg"))
    if not frames and shot.source:
        frames = [folder.parent / k for k in shot.source.keyframes]
    return [str(f) for f in frames if Path(f).is_file()]


def start_reparse(session: Session, project_id: str, shot_id: str, language: str) -> Job:
    try:
        doc = latest_doc(session, project_id)
        selection = selection_for(session, Capability.VIDEO_UNDERSTANDING)
    except VersionError as exc:
        raise ReparseError(str(exc), error_type=exc.error_type) from exc
    except CapabilityUnavailable as exc:
        raise ReparseError(str(exc), error_type="not_configured") from exc
    shot = next((s for s in doc.shots if s.shot_id == shot_id), None)
    if shot is None:
        raise ReparseError(f"No shot {shot_id}", error_type="not_found")
    if shot.source is None:
        raise ReparseError("A new shot has no footage to describe")
    frames = _frames(project_id, shot)
    if not frames:
        raise ReparseError("This shot's frames are missing; parse the video again")
    return enqueue_job(
        session,
        capability=Capability.VIDEO_UNDERSTANDING,
        provider=selection.provider or "",
        model=selection.model,
        project_id=project_id,
        shot_id=shot_id,
        purpose=PURPOSE,
        request={
            "language": language,
            "shots": [
                {
                    "shot_id": shot_id,
                    "start": shot.source.start,
                    "end": shot.source.end,
                    "frames": frames,
                }
            ],
            "characters": [
                c.model_dump(include={"character_id", "name", "description"})
                for c in doc.characters
            ],
            "scenes": [s.model_dump(include={"scene_id", "description"}) for s in doc.scenes],
        },
    )


def _redescribe(doc: StoryboardDoc, shot_id: str, result: UnderstandingResult) -> StoryboardDoc:
    described = next((s for s in result.shots if s.shot_id == shot_id), None)
    if described is None:
        raise ReparseError("The model did not describe this shot", error_type="parse_error")
    shots = []
    for shot in doc.shots:
        if shot.shot_id != shot_id or shot.source is None:
            shots.append(shot)
            continue
        prepared = PreparedShot(
            shot_id=shot.shot_id,
            start=shot.source.start,
            end=shot.source.end,
            clip=shot.source.clip or "",
            keyframes=shot.source.keyframes,
            frames=[],
        )
        fresh = _shot(
            prepared,
            described,
            {c.character_id for c in doc.characters},
            {s.scene_id for s in doc.scenes},
        )
        shots.append(
            shot.model_copy(
                update={
                    "narrative": fresh.narrative,
                    "visual": fresh.visual,
                    "assets": fresh.assets,
                }
            )
        )
    return doc.model_copy(update={"shots": shots})


def on_job_finished(session: Session, job: Job) -> None:
    if job.purpose != PURPOSE or job.project_id is None or job.shot_id is None:
        return
    if job.status != JobStatus.SUCCEEDED:
        return
    try:
        result = UnderstandingResult.model_validate((job.result or {}).get("data") or {})
        base = _redescribe(baseline_doc(session, job.project_id), job.shot_id, result)
        latest = _redescribe(latest_doc(session, job.project_id), job.shot_id, result)
    except (ValidationError, ReparseError, VersionError) as exc:
        logger.warning("re-describing %s failed: %s", job.shot_id, exc)
        job.error_type, job.error = "parse_error", str(exc)
        session.add(job)
        session.commit()
        return
    note = f"Described {job.shot_id} again"
    version = next_version(session, job.project_id)
    session.add(
        Storyboard(
            project_id=job.project_id,
            version=version,
            content=base.model_copy(update={"version": version}).model_dump(mode="json"),
            source=StoryboardSource.AUTO_PARSE,
            note=note,
        )
    )
    session.commit()
    save_version(session, job.project_id, latest, source=StoryboardSource.USER_EDIT, note=note)
