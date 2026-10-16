"""Parse a source video into storyboard v1 (docs/PLAN.md §7 steps 1–2).

1. :func:`prepare_shots` (local, CPU): detect cuts, slice each shot into its own clip,
   pull 3 keyframes for the UI and a few smaller frames for the model.
2. :func:`run_parse` queues one ``video_understanding`` job. Timestamps come from the
   detector, never from the model, so they are exact to the frame.
3. :func:`on_job_finished` (a worker hook) merges the model's descriptions with the
   detected shots, validates the result and saves it as a new storyboard version.
"""

import asyncio
import logging
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.capabilities.base import Capability
from app.capabilities.video_understanding import ShotOut, UnderstandingResult
from app.models import Job, JobStatus, Project, ProjectStatus, Storyboard, StoryboardSource
from app.pipeline.jobs import enqueue_job
from app.providers.settings import CapabilityUnavailable, selection_for
from app.storage.local import project_dir
from app.storyboard.schema import (
    Character,
    CharacterAction,
    DialogueLine,
    Narrative,
    Scene,
    Shot,
    ShotAssets,
    ShotSource,
    StoryboardDoc,
    Visual,
)
from app.storyboard.versions import next_version
from app.tools.media import MediaError, VideoInfo, cut_clip, extract_frame, probe, sample_times
from app.tools.scene_detect import (
    DEFAULT_THRESHOLD,
    MAX_THRESHOLD,
    MIN_THRESHOLD,
    ShotSpan,
    detect_shots,
)

logger = logging.getLogger(__name__)

PURPOSE = "parse"
KEYFRAMES_PER_SHOT = 3
KEYFRAME_HEIGHT = 720
ANALYSIS_HEIGHT = 360
ANALYSIS_FPS = 1.5
ANALYSIS_FRAMES = (4, 8)  # min, max per shot
PREP_WORKERS = 4
GENERATED_DIRS = ("clips", "frames")


class ParseOptions(BaseModel):
    threshold: float = Field(DEFAULT_THRESHOLD, ge=MIN_THRESHOLD, le=MAX_THRESHOLD)
    language: Literal["en", "zh-CN"] = "en"


class PreparedShot(BaseModel):
    shot_id: str
    start: float
    end: float
    clip: str  # relative to the project dir
    keyframes: list[str]  # relative to the project dir
    frames: list[str]  # absolute paths of the smaller frames sent to the model


class ParseError(Exception):
    def __init__(self, message: str, *, error_type: str = "parse_error") -> None:
        super().__init__(message)
        self.error_type = error_type


# ---------------------------------------------------------------------------
# Step 1: local preparation
# ---------------------------------------------------------------------------


def shot_id_for(index: int) -> str:
    return f"s{index + 1:03d}"


def analysis_frame_count(duration: float) -> int:
    low, high = ANALYSIS_FRAMES
    return max(low, min(high, round(duration * ANALYSIS_FPS)))


def _prepare_one(root: Path, source: Path, index: int, span: ShotSpan) -> PreparedShot:
    shot_id = shot_id_for(index)
    clip = Path("clips") / f"{shot_id}.mp4"
    cut_clip(source, span.start, span.end, root / clip)
    keyframes = [
        extract_frame(
            source, at, root / "frames" / f"{shot_id}_{i}.jpg", max_height=KEYFRAME_HEIGHT
        ).relative_to(root)
        for i, at in enumerate(sample_times(span.start, span.end, KEYFRAMES_PER_SHOT))
    ]
    times = sample_times(span.start, span.end, analysis_frame_count(span.duration))
    frames = [
        extract_frame(
            source,
            at,
            root / "frames" / "analysis" / f"{shot_id}_{i:02d}.jpg",
            max_height=ANALYSIS_HEIGHT,
        )
        for i, at in enumerate(times)
    ]
    return PreparedShot(
        shot_id=shot_id,
        start=span.start,
        end=span.end,
        clip=clip.as_posix(),
        keyframes=[k.as_posix() for k in keyframes],
        frames=[str(f) for f in frames],
    )


def prepare_shots(
    project_id: str, source: Path, info: VideoInfo, threshold: float
) -> list[PreparedShot]:
    """Detect, slice and sample. Replaces clips and frames from any earlier parse."""
    root = project_dir(project_id)
    for name in GENERATED_DIRS:
        shutil.rmtree(root / name, ignore_errors=True)
        (root / name).mkdir(parents=True, exist_ok=True)
    spans = detect_shots(source, duration=info.duration, fps=info.fps, threshold=threshold)
    with ThreadPoolExecutor(max_workers=PREP_WORKERS) as pool:
        return list(pool.map(lambda pair: _prepare_one(root, source, *pair), enumerate(spans)))


# ---------------------------------------------------------------------------
# Step 2: queue the provider call
# ---------------------------------------------------------------------------


def _set_project(engine: Engine, project_id: str, **changes: Any) -> None:
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if project is None:
            return
        project.sqlmodel_update(changes)
        session.add(project)
        session.commit()


def fail_project(engine: Engine, project_id: str, error_type: str, message: str) -> None:
    _set_project(
        engine,
        project_id,
        status=ProjectStatus.FAILED,
        stage=None,
        error_type=error_type,
        error=message,
    )


def begin_parse(session: Session, project: Project) -> None:
    """Mark the project as parsing. Raises :class:`ParseError` if it cannot start."""
    if project.source_video_path is None:
        raise ParseError("Upload a video first", error_type="input_invalid")
    if project.status == ProjectStatus.PARSING:
        raise ParseError("This project is already being parsed", error_type="conflict")
    try:
        selection_for(session, Capability.VIDEO_UNDERSTANDING)
    except CapabilityUnavailable as exc:
        raise ParseError(str(exc), error_type="not_configured") from exc
    project.sqlmodel_update(
        {"status": ProjectStatus.PARSING, "stage": "detecting", "error_type": None, "error": None}
    )
    session.add(project)
    session.commit()


async def run_parse(engine: Engine, project_id: str, options: ParseOptions) -> None:
    """Prepare shots off the event loop, then queue the understanding job."""
    with Session(engine) as session:
        project = session.get(Project, project_id)
        if project is None or project.source_video_path is None:
            return
        source = Path(project.source_video_path)
    try:
        info = await asyncio.to_thread(probe, source)
        prepared = await asyncio.to_thread(
            prepare_shots, project_id, source, info, options.threshold
        )
    except MediaError as exc:
        fail_project(engine, project_id, "media_error", str(exc))
        return
    except Exception:
        logger.exception("shot preparation failed for project %s", project_id)
        fail_project(engine, project_id, "media_error", "Preparing the shots failed unexpectedly")
        return

    # Set before queueing: the worker may finish the job before this function returns.
    _set_project(engine, project_id, stage="describing")
    with Session(engine) as session:
        try:
            selection = selection_for(session, Capability.VIDEO_UNDERSTANDING)
        except CapabilityUnavailable as exc:
            fail_project(engine, project_id, "not_configured", str(exc))
            return
        enqueue_job(
            session,
            capability=Capability.VIDEO_UNDERSTANDING,
            provider=selection.provider or "",
            model=selection.model,
            project_id=project_id,
            purpose=PURPOSE,
            request={
                "language": options.language,
                "shots": [shot.model_dump() for shot in prepared],
            },
        )


# ---------------------------------------------------------------------------
# Step 3: apply the result
# ---------------------------------------------------------------------------


def _shot(
    prepared: PreparedShot, described: ShotOut | None, characters: set[str], scenes: set[str]
) -> Shot:
    actions = [a for a in described.characters if a.character_id in characters] if described else []
    lines = described.dialogue if described else []
    scene_id = described.scene_id if described and described.scene_id in scenes else None
    visual = described.visual.model_dump() if described else {}
    return Shot(
        shot_id=prepared.shot_id,
        source=ShotSource(
            clip=prepared.clip, start=prepared.start, end=prepared.end, keyframes=prepared.keyframes
        ),
        narrative=Narrative(
            description=described.description if described else "",
            characters=[CharacterAction(**a.model_dump()) for a in actions],
            dialogue=[
                DialogueLine(
                    character_id=line.character_id if line.character_id in characters else None,
                    text=line.text,
                )
                for line in lines
                if line.text.strip()
            ],
        ),
        visual=Visual(**visual),
        assets=ShotAssets(
            scene_id=scene_id,
            character_ids=list(dict.fromkeys(a.character_id for a in actions)),
        ),
    )


def build_storyboard(
    project_id: str, version: int, prepared: list[PreparedShot], result: UnderstandingResult
) -> StoryboardDoc:
    """Detected shots are the source of truth; the model only fills in descriptions.

    Shots the model skipped stay with empty descriptions. Characters a shot mentions
    but the model never declared are added so every reference resolves.
    """
    described = {shot.shot_id: shot for shot in result.shots}
    declared = {c.character_id: c for c in result.characters}
    mentioned = [a.character_id for shot in result.shots for a in shot.characters if a.character_id]
    undeclared = [cid for cid in dict.fromkeys(mentioned) if cid not in declared]
    character_docs = [
        *(
            Character(
                character_id=c.character_id,
                name=c.name or c.character_id,
                description=c.description,
            )
            for c in declared.values()
        ),
        *(Character(character_id=cid, name=cid) for cid in undeclared),
    ]
    scene_docs = list(
        {
            s.scene_id: Scene(scene_id=s.scene_id, description=s.description) for s in result.scenes
        }.values()
    )
    known_characters = {c.character_id for c in character_docs}
    known_scenes = {s.scene_id for s in scene_docs}
    return StoryboardDoc(
        project_id=project_id,
        version=version,
        characters=character_docs,
        scenes=scene_docs,
        shots=[
            _shot(p, described.get(p.shot_id), known_characters, known_scenes) for p in prepared
        ],
    )


def on_job_finished(session: Session, job: Job) -> None:
    """Worker hook: turn a finished parse job into storyboard v-next."""
    if job.purpose != PURPOSE or job.project_id is None:
        return
    project = session.get(Project, job.project_id)
    if project is None:
        return
    if job.status != JobStatus.SUCCEEDED:
        _apply(
            session,
            project,
            status=ProjectStatus.FAILED,
            stage=None,
            error_type=job.error_type or "provider_error",
            error=job.error,
        )
        return
    try:
        prepared = [PreparedShot.model_validate(s) for s in job.request.get("shots", [])]
        result = UnderstandingResult.model_validate((job.result or {}).get("data") or {})
        doc = build_storyboard(project.id, next_version(session, project.id), prepared, result)
    except ValidationError as exc:
        logger.warning("parse result for project %s is invalid: %s", project.id, exc)
        _apply(
            session,
            project,
            status=ProjectStatus.FAILED,
            stage=None,
            error_type="parse_error",
            error="The model's storyboard could not be validated",
        )
        return
    session.add(
        Storyboard(
            project_id=project.id,
            version=doc.version,
            content=doc.model_dump(mode="json"),
            source=StoryboardSource.AUTO_PARSE,
        )
    )
    _apply(session, project, status=ProjectStatus.READY, stage=None, error_type=None, error=None)


def _apply(session: Session, project: Project, **changes: Any) -> None:
    project.sqlmodel_update(changes)
    session.add(project)
    session.commit()


def recover_interrupted(session: Session) -> int:
    """After a restart, fail projects stuck in ``parsing`` with no job left to finish them."""
    active = {
        job.project_id
        for job in session.exec(
            select(Job).where(
                Job.purpose == PURPOSE, Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING])
            )
        )
    }
    stuck = session.exec(select(Project).where(Project.status == ProjectStatus.PARSING)).all()
    recovered = 0
    for project in stuck:
        if project.id in active:
            continue
        project.sqlmodel_update(
            {
                "status": ProjectStatus.FAILED,
                "stage": None,
                "error_type": "interrupted",
                "error": "Parsing was interrupted when the app stopped; please parse again",
            }
        )
        session.add(project)
        recovered += 1
    session.commit()
    return recovered
