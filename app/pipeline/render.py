"""Generate the changed shots and stitch the new video (docs/PLAN.md §7 steps 5–7, M4).

A :class:`~app.models.Render` runs one storyboard version. Each shot walks a small state
machine, lightest treatment first:

- ``keep``: the source clip, no API call. ``removed``: skipped.
- ``trim``: cut locally.
- ``swap`` / ``edit``: ``video_edit_v2v`` on the source clip (padded or split to fit).
- ``motion``: ``image_edit`` → first frame → ``video_edit_v2v`` (motion transfer).
- ``regenerate`` / ``new``: ``image_edit`` → first frame → ``image_to_video``.

First frames are cheap and videos are not, so by default a shot pauses on its first frame
until the user approves it. Every provider call is a worker job (purpose ``render``);
:func:`on_job_finished` saves its result and moves the shot on. When every shot is done,
the clips are normalized and stitched with the original soundtrack.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.capabilities.base import Capability
from app.models import Job, JobStatus, Project, ProjectStatus, Render, RenderStatus, utcnow
from app.pipeline import render_plan as plan
from app.pipeline.artifacts import ArtifactMissing, save_artifact, suffix_for
from app.pipeline.jobs import enqueue_job
from app.providers.registry import Registry, get_registry
from app.providers.settings import CapabilityUnavailable, selection_for
from app.storage.local import project_dir
from app.storyboard.schema import Shot, StoryboardDoc, Treatment
from app.storyboard.versions import VersionError, baseline_doc, latest_doc, load, row_for
from app.tools.media import (
    MediaError,
    Segment,
    concat_clips,
    cut_clip,
    extract_frame,
    label_clip,
    normalize_clip,
    pad_clip,
    probe,
    split_points,
    stitch,
)

logger = logging.getLogger(__name__)

PURPOSE = "render"
AI_LABEL = "AI-generated"
AI_COMMENT = "Contains AI-generated shots (made with Copy-Right)"
ACTIVE = (RenderStatus.RUNNING, RenderStatus.WAITING, RenderStatus.STITCHING)
NEEDS = {
    Treatment.SWAP: (Capability.VIDEO_EDIT_V2V,),
    Treatment.EDIT: (Capability.VIDEO_EDIT_V2V,),
    Treatment.MOTION: (Capability.IMAGE_EDIT, Capability.VIDEO_EDIT_V2V),
    Treatment.REGENERATE: (Capability.IMAGE_EDIT, Capability.IMAGE_TO_VIDEO),
    Treatment.NEW: (Capability.IMAGE_EDIT, Capability.IMAGE_TO_VIDEO),
}

ShotStatus = Literal["pending", "running", "frame_ready", "done", "skipped", "failed"]
Step = Literal["frame", "video", "i2v"]


class RenderError(Exception):
    def __init__(self, message: str, *, error_type: str = "input_invalid") -> None:
        super().__init__(message)
        self.error_type = error_type


class ShotState(BaseModel):
    treatment: Treatment
    status: ShotStatus = "pending"
    step: Step | None = None
    duration: float = 0.0  # seconds this shot lasts in the output
    mode: str | None = None  # video_edit_v2v request mode
    pieces: int = 0
    piece_clips: dict[str, str] = Field(default_factory=dict)
    first_frame: str | None = None  # project-relative
    clip: str | None = None  # project-relative clip used for stitching
    jobs: list[str] = Field(default_factory=list)
    # Per-shot provider override after a failure, e.g. to get past moderation.
    provider: str | None = None
    model: str | None = None
    prompt: str | None = None  # user's override for the first-frame prompt
    error_type: str | None = None
    error: str | None = None


class RenderOptions(BaseModel):
    quality: Literal["preview", "final"] = "preview"
    approve_frames: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _states(render: Render) -> dict[str, ShotState]:
    return {sid: ShotState.model_validate(raw) for sid, raw in render.shots.items()}


def _store(session: Session, render: Render, states: dict[str, ShotState], **changes: Any) -> None:
    render.sqlmodel_update(
        {
            **changes,
            "shots": {sid: s.model_dump(mode="json") for sid, s in states.items()},
            "updated_at": utcnow(),
        }
    )
    session.add(render)
    session.commit()
    session.refresh(render)


def _work_dir(render: Render) -> Path:
    path = project_dir(render.project_id) / "results" / "renders" / render.id
    path.mkdir(parents=True, exist_ok=True)
    return path


def _rel(render: Render, path: Path) -> str:
    return path.relative_to(project_dir(render.project_id)).as_posix()


def _doc(session: Session, render: Render) -> StoryboardDoc:
    row = row_for(session, render.project_id, render.storyboard_version)
    if row is None:
        raise RenderError("The storyboard version of this render is gone", error_type="not_found")
    return load(row)


def _shot_duration(shot: Shot) -> float:
    if shot.edit.treatment == Treatment.TRIM and shot.edit.trim:
        return shot.edit.trim.keep_end - shot.edit.trim.keep_start
    if shot.source:
        return shot.source.end - shot.source.start
    return shot.generation.duration or 4.0


class _Context:
    """What a step needs, loaded once per call."""

    def __init__(self, session: Session, render: Render, registry: Registry) -> None:
        self.session = session
        self.render = render
        self.registry = registry
        self.doc = _doc(session, render)
        self.base = baseline_doc(session, render.project_id)
        self.root = project_dir(render.project_id)
        self.work = _work_dir(render)
        self.shots = {s.shot_id: s for s in self.doc.shots}
        project = session.get(Project, render.project_id)
        self.source = (
            Path(project.source_video_path) if project and project.source_video_path else None
        )

    def selection(self, capability: Capability, state: ShotState) -> tuple[str, str | None]:
        if state.provider and capability == _capability_for(state):
            return state.provider, state.model
        chosen = selection_for(self.session, capability, self.registry)
        return chosen.provider or "", chosen.model


def _capability_for(state: ShotState) -> Capability:
    """The capability of the step a failed shot will retry."""
    if state.step == "frame":
        return Capability.IMAGE_EDIT
    if state.step == "i2v":
        return Capability.IMAGE_TO_VIDEO
    return Capability.VIDEO_EDIT_V2V


def _enqueue(
    ctx: _Context,
    shot_id: str,
    state: ShotState,
    capability: Capability,
    request: dict,
    **meta: Any,
) -> str:
    provider, model = ctx.selection(capability, state)
    job = enqueue_job(
        ctx.session,
        capability=capability,
        provider=provider,
        model=meta.pop("model", None) or model,
        project_id=ctx.render.project_id,
        shot_id=shot_id,
        purpose=PURPOSE,
        request={**request, "render": {"id": ctx.render.id, **meta}},
    )
    return job.id


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------


def _fail(state: ShotState, message: str, error_type: str = "input_invalid") -> ShotState:
    return state.model_copy(update={"status": "failed", "error_type": error_type, "error": message})


def _begin(ctx: _Context, shot: Shot, state: ShotState) -> ShotState:
    treatment = state.treatment
    if treatment == Treatment.REMOVED:
        return state.model_copy(update={"status": "skipped"})
    if treatment == Treatment.KEEP:
        return state.model_copy(
            update={"status": "done", "clip": shot.source.clip if shot.source else None}
        )
    if treatment == Treatment.TRIM:
        return _trim(ctx, shot, state)
    if treatment in (Treatment.SWAP, Treatment.EDIT):
        return _start_video(ctx, shot, state)
    return _start_frame(ctx, shot, state)


def _trim(ctx: _Context, shot: Shot, state: ShotState) -> ShotState:
    if ctx.source is None or shot.edit.trim is None:
        return _fail(state, "Nothing to trim: set how much of the shot to keep")
    out = ctx.work / f"{shot.shot_id}_trim.mp4"
    try:
        cut_clip(ctx.source, shot.edit.trim.keep_start, shot.edit.trim.keep_end, out)
    except MediaError as exc:
        return _fail(state, str(exc), "media_error")
    return state.model_copy(update={"status": "done", "clip": _rel(ctx.render, out)})


def _clip_pieces(ctx: _Context, shot_id: str, clip: Path, choice: plan.ModelChoice) -> list[Path]:
    """Pad a short clip or split a long one to fit the model's input limits."""
    duration = probe(clip).duration
    if choice.min_seconds and duration < choice.min_seconds:
        return [pad_clip(clip, ctx.work / f"{shot_id}_padded.mp4", min_duration=choice.min_seconds)]
    if choice.max_seconds and duration > choice.max_seconds:
        return [
            cut_clip(clip, start, end, ctx.work / f"{shot_id}_part{i}.mp4")
            for i, (start, end) in enumerate(split_points(duration, choice.max_seconds))
        ]
    return [clip]


def _start_video(ctx: _Context, shot: Shot, state: ShotState) -> ShotState:
    """``video_edit_v2v`` on the source clip (swap, edit) or with a first frame (motion)."""
    if shot.source is None or not shot.source.clip:
        return _fail(state, "This shot has no source clip")
    looks = plan.looks_for(ctx.base, ctx.doc, shot, ctx.root)
    if state.treatment == Treatment.SWAP and not looks:
        return _fail(state, "Give a character or scene in this shot a new look first")
    provider, model = ctx.selection(Capability.VIDEO_EDIT_V2V, state)
    treatment = state.treatment.value
    choice = plan.choose_video_model(ctx.registry, provider, model, treatment)
    if choice is None:
        return _fail(state, f"{provider} has no video model for {treatment}", "not_configured")
    try:
        pieces = _clip_pieces(ctx, shot.shot_id, ctx.root / shot.source.clip, choice)
    except MediaError as exc:
        return _fail(state, str(exc), "media_error")
    first_frame = ctx.root / state.first_frame if state.first_frame else None
    jobs = []
    for index, piece in enumerate(pieces):
        if choice.mode == "motion" and first_frame is not None:
            request = plan.motion_request(piece, first_frame, ctx.render.quality)
        else:
            request = plan.video_edit_request(piece, shot, looks, choice.mode, ctx.render.quality)
        jobs.append(
            _enqueue(
                ctx,
                shot.shot_id,
                state,
                Capability.VIDEO_EDIT_V2V,
                request,
                model=choice.model,
                shot=shot.shot_id,
                step="video",
                piece=index,
            )
        )
    return state.model_copy(
        update={
            "status": "running",
            "step": "video",
            "mode": choice.mode,
            "pieces": len(pieces),
            "piece_clips": {},
            "jobs": [*state.jobs, *jobs],
            "error_type": None,
            "error": None,
        }
    )


def _base_frame(ctx: _Context, shot: Shot) -> Path | None:
    """The frame a new first frame is drawn from: this shot's opening frame, or for a new
    shot the last keyframe of the nearest earlier shot with footage."""
    if shot.source and ctx.source:
        return extract_frame(
            ctx.source, shot.source.start + 0.05, ctx.work / f"{shot.shot_id}_source.jpg"
        )
    order = [s.shot_id for s in ctx.doc.shots]
    for earlier in reversed(ctx.doc.shots[: order.index(shot.shot_id)]):
        if earlier.source and earlier.source.keyframes:
            return ctx.root / earlier.source.keyframes[-1]
    return None


def _start_frame(ctx: _Context, shot: Shot, state: ShotState) -> ShotState:
    looks = plan.looks_for(ctx.base, ctx.doc, shot, ctx.root)
    try:
        base = _base_frame(ctx, shot)
    except MediaError as exc:
        return _fail(state, str(exc), "media_error")
    request = plan.frame_request(shot, looks, base, new=shot.source is None)
    if state.prompt:
        request = {**request, "prompt": f"{state.prompt} {request['prompt']}"[:2000]}
    if not request["images"]:
        return _fail(state, "There is no frame or reference image to start from")
    job = _enqueue(
        ctx, shot.shot_id, state, Capability.IMAGE_EDIT, request, shot=shot.shot_id, step="frame"
    )
    return state.model_copy(
        update={
            "status": "running",
            "step": "frame",
            "jobs": [*state.jobs, job],
            "error_type": None,
            "error": None,
        }
    )


def _start_after_frame(ctx: _Context, shot: Shot, state: ShotState) -> ShotState:
    if state.treatment == Treatment.MOTION:
        return _start_video(ctx, shot, state)
    if state.first_frame is None:
        return _fail(state, "The first frame is missing")
    request = plan.i2v_request(
        shot, ctx.doc, ctx.root / state.first_frame, state.duration, ctx.render.quality
    )
    job = _enqueue(
        ctx, shot.shot_id, state, Capability.IMAGE_TO_VIDEO, request, shot=shot.shot_id, step="i2v"
    )
    return state.model_copy(
        update={
            "status": "running",
            "step": "i2v",
            "jobs": [*state.jobs, job],
            "error_type": None,
            "error": None,
        }
    )


# ---------------------------------------------------------------------------
# Public operations
# ---------------------------------------------------------------------------


def active_render(session: Session, project_id: str) -> Render | None:
    return session.exec(
        select(Render)
        .where(Render.project_id == project_id, Render.status.in_(ACTIVE))
        .order_by(Render.created_at.desc())
    ).first()


def _check_capabilities(session: Session, doc: StoryboardDoc) -> None:
    needed = {cap for s in doc.shots for cap in NEEDS.get(s.edit.treatment, ())}
    missing = []
    for capability in sorted(needed):
        try:
            selection_for(session, capability)
        except CapabilityUnavailable:
            missing.append(capability.value)
    if missing:
        raise RenderError(
            f"Choose a provider for {', '.join(missing)} in Settings", error_type="not_configured"
        )


def start_render(
    session: Session, project_id: str, options: RenderOptions, registry: Registry | None = None
) -> Render:
    project = session.get(Project, project_id)
    if project is None:
        raise RenderError("Project not found", error_type="not_found")
    if active_render(session, project_id):
        raise RenderError("A render is already running for this project", error_type="conflict")
    try:
        doc = latest_doc(session, project_id)
    except VersionError as exc:
        raise RenderError(str(exc), error_type=exc.error_type) from exc
    _check_capabilities(session, doc)
    render = Render(
        project_id=project_id,
        storyboard_version=doc.version,
        quality=options.quality,
        approve_frames=options.approve_frames,
        shots={
            s.shot_id: ShotState(treatment=s.edit.treatment, duration=_shot_duration(s)).model_dump(
                mode="json"
            )
            for s in doc.shots
        },
    )
    session.add(render)
    session.commit()
    session.refresh(render)
    ctx = _Context(session, render, registry or get_registry())
    states = {sid: _begin(ctx, ctx.shots[sid], state) for sid, state in _states(render).items()}
    _store(session, render, states)
    project.sqlmodel_update({"status": ProjectStatus.GENERATING})
    session.add(project)
    session.commit()
    session.refresh(render)
    _settle(session, render)
    return render


def _shot_action(session: Session, render: Render, shot_id: str) -> tuple[_Context, ShotState]:
    if render.status not in ACTIVE:
        raise RenderError("This render has finished", error_type="conflict")
    state = _states(render).get(shot_id)
    if state is None:
        raise RenderError(f"No shot {shot_id} in this render", error_type="not_found")
    return _Context(session, render, get_registry()), state


def approve_frame(session: Session, render: Render, shot_id: str) -> Render:
    ctx, state = _shot_action(session, render, shot_id)
    if state.status != "frame_ready":
        raise RenderError(
            "This shot has no first frame waiting for approval", error_type="conflict"
        )
    _update_shot(session, render, shot_id, _start_after_frame(ctx, ctx.shots[shot_id], state))
    return render


def redo_frame(session: Session, render: Render, shot_id: str, prompt: str | None) -> Render:
    ctx, state = _shot_action(session, render, shot_id)
    if state.status not in ("frame_ready", "failed") or state.treatment not in (
        Treatment.MOTION,
        Treatment.REGENERATE,
        Treatment.NEW,
    ):
        raise RenderError("Only a waiting first frame can be redone", error_type="conflict")
    fresh = state.model_copy(update={"prompt": (prompt or "").strip() or None, "first_frame": None})
    _update_shot(session, render, shot_id, _start_frame(ctx, ctx.shots[shot_id], fresh))
    return render


def retry_shot(
    session: Session,
    render: Render,
    shot_id: str,
    provider: str | None = None,
    model: str | None = None,
) -> Render:
    """Run a failed shot again from its failed step, optionally with another provider
    for that step (e.g. after ``content_rejected``)."""
    ctx, state = _shot_action(session, render, shot_id)
    if state.status != "failed":
        raise RenderError("Only failed shots can be retried", error_type="conflict")
    if provider is not None:
        spec = ctx.registry.providers.get(provider)
        capability = _capability_for(state) if state.step else Capability.VIDEO_EDIT_V2V
        if spec is None or capability not in spec.capabilities or not spec.implemented:
            raise RenderError(f"{provider} cannot do {capability}")
    fresh = state.model_copy(update={"provider": provider, "model": model, "status": "pending"})
    shot = ctx.shots[shot_id]
    if state.step == "i2v" or (state.step == "video" and state.treatment == Treatment.MOTION):
        next_state = _start_after_frame(ctx, shot, fresh)
    elif state.step == "video":
        next_state = _start_video(ctx, shot, fresh)
    elif state.step == "frame":
        next_state = _start_frame(ctx, shot, fresh)
    else:
        next_state = _begin(ctx, shot, fresh)
    _update_shot(session, render, shot_id, next_state)
    return render


def cancel_render(session: Session, render: Render) -> Render:
    """Stop waiting on this render. Remote tasks already running cannot be recalled."""
    if render.status not in ACTIVE:
        raise RenderError("This render has finished", error_type="conflict")
    _store(session, render, _states(render), status=RenderStatus.CANCELLED)
    _set_project_status(session, render.project_id, ProjectStatus.READY)
    session.refresh(render)
    return render


def _update_shot(session: Session, render: Render, shot_id: str, state: ShotState) -> None:
    states = {**_states(render), shot_id: state}
    _store(session, render, states, status=RenderStatus.RUNNING)
    _settle(session, render)


def _set_project_status(session: Session, project_id: str, status: ProjectStatus) -> None:
    project = session.get(Project, project_id)
    if project is not None:
        project.sqlmodel_update({"status": status})
        session.add(project)
        session.commit()


def _settle(session: Session, render: Render) -> None:
    """Move the render on: stitch when every shot is ready, wait when only the user can
    unblock it, otherwise keep running."""
    if render.status not in ACTIVE or render.status == RenderStatus.STITCHING:
        return
    statuses = {s.status for s in _states(render).values()}
    if statuses <= {"done", "skipped"}:
        if "done" not in statuses:
            _store(
                session,
                render,
                _states(render),
                status=RenderStatus.FAILED,
                error="Every shot was removed",
            )
            return
        _store(session, render, _states(render), status=RenderStatus.STITCHING)
        _schedule_stitch(render.id)
    elif not statuses & {"pending", "running"}:
        _store(session, render, _states(render), status=RenderStatus.WAITING)


# ---------------------------------------------------------------------------
# Worker hook
# ---------------------------------------------------------------------------


async def on_job_finished(session: Session, job: Job) -> None:
    meta = (job.request or {}).get("render") if job.purpose == PURPOSE else None
    if not meta:
        return
    render = session.get(Render, meta.get("id"))
    shot_id = meta.get("shot")
    if render is None or render.status not in ACTIVE or shot_id not in render.shots:
        return
    state = _states(render)[shot_id]
    if job.id not in state.jobs or state.status != "running":
        return  # a superseded job (the shot was retried or redone)
    if job.status != JobStatus.SUCCEEDED:
        _update_shot(
            session,
            render,
            shot_id,
            _fail(state, job.error or "Failed", job.error_type or "provider_error"),
        )
        return
    step, piece = meta.get("step"), int(meta.get("piece") or 0)
    default = ".png" if step == "frame" else ".mp4"
    name = f"{shot_id}_{step}{piece if state.pieces > 1 else ''}_{job.id[:6]}{suffix_for(job.result, default)}"
    target = _work_dir(render) / name
    try:
        await save_artifact(job.result, target)
    except ArtifactMissing as exc:
        _update_shot(session, render, shot_id, _fail(state, str(exc), "provider_error"))
        return
    session.refresh(render)
    state = _states(render)[shot_id]
    _update_shot(
        session, render, shot_id, _advance(session, render, shot_id, state, step, piece, target)
    )


def _advance(
    session: Session,
    render: Render,
    shot_id: str,
    state: ShotState,
    step: str,
    piece: int,
    saved: Path,
) -> ShotState:
    relative = _rel(render, saved)
    if step == "frame":
        framed = state.model_copy(update={"first_frame": relative})
        if render.approve_frames:
            return framed.model_copy(update={"status": "frame_ready"})
        ctx = _Context(session, render, get_registry())
        return _start_after_frame(ctx, ctx.shots[shot_id], framed)
    if step == "i2v":
        return state.model_copy(update={"status": "done", "clip": relative})
    pieces = {**state.piece_clips, str(piece): relative}
    if len(pieces) < state.pieces:
        return state.model_copy(update={"piece_clips": pieces})
    clips = [project_dir(render.project_id) / pieces[str(i)] for i in range(state.pieces)]
    if len(clips) == 1:
        return state.model_copy(update={"status": "done", "piece_clips": pieces, "clip": relative})
    try:
        joined = _join(clips, _work_dir(render) / f"{shot_id}_joined.mp4")
    except MediaError as exc:
        return _fail(state, str(exc), "media_error")
    return state.model_copy(
        update={"status": "done", "piece_clips": pieces, "clip": _rel(render, joined)}
    )


def _join(clips: list[Path], out: Path) -> Path:
    """Join the pieces of a split shot, matching them to the first piece's size."""
    first = probe(clips[0])
    normalized = [
        normalize_clip(
            clip,
            clip.with_suffix(".norm.mp4"),
            width=first.width,
            height=first.height,
            fps=first.fps or 25,
            duration=probe(clip).duration,
        )
        for clip in clips
    ]
    return concat_clips(normalized, out)


# ---------------------------------------------------------------------------
# Stitching
# ---------------------------------------------------------------------------

_STITCH_TASKS: set[asyncio.Task[None]] = set()
_ENGINE: Engine | None = None


def bind_engine(engine: Engine) -> None:
    """Stitching runs outside the request or worker session; it opens its own."""
    global _ENGINE
    _ENGINE = engine


def _schedule_stitch(render_id: str) -> None:
    if _ENGINE is None:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (e.g. a sync test): call stitch_render directly
    task = loop.create_task(asyncio.to_thread(stitch_render, _ENGINE, render_id))
    _STITCH_TASKS.add(task)
    task.add_done_callback(_STITCH_TASKS.discard)


def segments_for(render: Render, doc: StoryboardDoc, project: Project, work: Path) -> list[Segment]:
    root = project_dir(render.project_id)
    states = _states(render)
    segments = []
    for shot in doc.shots:
        state = states.get(shot.shot_id)
        if state is None or state.status == "skipped" or state.clip is None:
            continue
        clip = root / state.clip
        generated = state.treatment not in (Treatment.KEEP, Treatment.TRIM)
        normalized = normalize_clip(
            clip,
            work / f"{shot.shot_id}_final.mp4",
            width=project.width or 1280,
            height=project.height or 720,
            fps=project.fps or 25,
            duration=state.duration,
        )
        if generated:
            normalized = label_clip(normalized, work / f"{shot.shot_id}_labelled.mp4", AI_LABEL)
        if state.treatment == Treatment.TRIM and shot.edit.trim:
            audio_start: float | None = shot.edit.trim.keep_start
        else:
            audio_start = shot.source.start if shot.source else None
        segments.append(Segment(clip=normalized, duration=state.duration, audio_start=audio_start))
    return segments


def stitch_render(engine: Engine, render_id: str) -> None:
    """Normalize every clip to the source's size and frame rate, label generated shots,
    and join them with the original soundtrack."""
    with Session(engine) as session:
        render = session.get(Render, render_id)
        if render is None or render.status != RenderStatus.STITCHING:
            return
        project = session.get(Project, render.project_id)
        try:
            doc = _doc(session, render)
            if project is None:
                raise RenderError("Project not found", error_type="not_found")
            work = _work_dir(render) / "final"
            work.mkdir(parents=True, exist_ok=True)
            segments = segments_for(render, doc, project, work)
            source = Path(project.source_video_path) if project.source_video_path else None
            out = stitch(
                segments, _work_dir(render) / "output.mp4", audio_source=source, comment=AI_COMMENT
            )
        except (MediaError, RenderError) as exc:
            logger.warning("stitching render %s failed: %s", render_id, exc)
            _store(
                session,
                render,
                _states(render),
                status=RenderStatus.FAILED,
                error_type="media_error",
                error=str(exc),
            )
            _set_project_status(session, render.project_id, ProjectStatus.READY)
            return
        _store(session, render, _states(render), status=RenderStatus.DONE, output=_rel(render, out))
        _set_project_status(session, render.project_id, ProjectStatus.DONE)


def recover(engine: Engine) -> None:
    """After a restart, finish renders that were stitching."""
    with Session(engine) as session:
        ids = [
            r.id
            for r in session.exec(select(Render).where(Render.status == RenderStatus.STITCHING))
        ]
    for render_id in ids:
        _schedule_stitch(render_id)
