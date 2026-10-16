"""Natural-language plot edits (docs/PLAN.md §7 step 4, §8 "剧情编辑").

1. :func:`start_plot_edit` queues one ``llm_text`` job with the storyboard and the
   instruction. The model answers with a list of *changes*, not a whole storyboard: that
   keeps replies short and makes every change reviewable on its own.
2. :func:`proposal_for` turns the finished job into a proposal: each change with its
   before/after text and suggested treatment.
3. :func:`apply_proposal` applies the changes the user accepted to the latest storyboard
   and saves it as a new ``llm_edit`` version.
"""

import json
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, ValidationError, field_validator
from sqlmodel import Session

from app.capabilities.base import Capability
from app.capabilities.llm_text import json_object
from app.capabilities.video_understanding import LANGUAGE_NAMES
from app.models import Job, JobStatus, StoryboardSource
from app.pipeline.jobs import enqueue_job
from app.providers.settings import CapabilityUnavailable, selection_for
from app.storyboard.schema import (
    Character,
    CharacterAction,
    DialogueLine,
    Narrative,
    Shot,
    ShotAssets,
    ShotEdit,
    ShotGeneration,
    StoryboardDoc,
    Treatment,
    Trim,
)
from app.storyboard.treatment import NARRATIVE_TREATMENTS
from app.storyboard.versions import VersionError, latest_doc, save_version

PURPOSE = "plot_edit"
DEFAULT_NEW_SHOT_S = 4.0
MAX_INSTRUCTION = 2000


class PlotEditError(Exception):
    def __init__(self, message: str, *, error_type: str = "input_invalid") -> None:
        super().__init__(message)
        self.error_type = error_type


# ---------------------------------------------------------------------------
# Model reply
# ---------------------------------------------------------------------------

Text = Annotated[str, BeforeValidator(lambda v: v if isinstance(v, str) else "")]
OptText = Annotated[str | None, BeforeValidator(lambda v: v if isinstance(v, str) else None)]
OptNumber = Annotated[
    float | None, BeforeValidator(lambda v: v if isinstance(v, (int, float)) else None)
]


class ActionIn(BaseModel):
    character_id: str
    action: OptText = None
    expression: OptText = None


class LineIn(BaseModel):
    character_id: OptText = None
    text: Text


class ChangeIn(BaseModel):
    action: Literal["change", "remove", "new"]
    shot_id: OptText = None  # the shot to change or remove
    after: OptText = None  # for "new": insert after this shot (None = at the start)
    description: OptText = None
    characters: list[ActionIn] | None = None
    dialogue: list[LineIn] | None = None
    scene_id: OptText = None
    treatment: OptText = None
    reason: OptText = None
    keep_seconds: OptNumber = None  # for "trim": keep this many seconds from the start
    duration: OptNumber = None  # for "new"

    @field_validator("action", mode="before")
    @classmethod
    def _lower(cls, value: Any) -> Any:
        return value.strip().lower() if isinstance(value, str) else value


class CharacterIn(BaseModel):
    character_id: str
    name: Text = ""
    description: Text = ""


class PlotEditReply(BaseModel):
    summary: Text = ""
    characters: list[CharacterIn] = Field(default_factory=list)
    changes: list[ChangeIn] = Field(default_factory=list)

    @field_validator("characters", "changes", mode="before")
    @classmethod
    def _drop_bad_items(cls, value: Any) -> list[Any]:
        return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def parse_reply(text: str) -> PlotEditReply:
    try:
        raw = json_object(text)
    except ValueError as exc:
        raise PlotEditError(str(exc), error_type="parse_error") from exc
    changes = []
    for item in raw.get("changes") or []:
        try:
            changes.append(ChangeIn.model_validate(item).model_dump())
        except ValidationError:
            continue  # one malformed change must not sink the others
    try:
        return PlotEditReply.model_validate({**raw, "changes": changes})
    except ValidationError as exc:
        raise PlotEditError(
            "The model's reply does not match the expected shape", error_type="parse_error"
        ) from exc


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

SYSTEM = """\
You are a film editor. You change a storyboard to follow the director's instruction while
keeping as much of the original footage as possible: change only the shots that must change.

For each change pick the LIGHTEST treatment that works:
- "trim": the shot only needs to end earlier; set keep_seconds (seconds kept from its start).
- "edit": same action and camera, small visual change (expression, weather, a prop).
- "motion": different content, but the movement is close to the original shot.
- "regenerate": the action is completely different.
Removed shots need no treatment; new shots are always generated from scratch.

Reply with one JSON object only:
{"summary": "one sentence on what changed",
 "characters": [{"character_id": "char_new_1", "name": "...", "description": "..."}],
 "changes": [
   {"action": "change", "shot_id": "s012", "description": "...", "characters":
    [{"character_id": "char_1", "action": "...", "expression": "..."}], "dialogue":
    [{"character_id": "char_1", "text": "..."}], "scene_id": "scene_1",
    "treatment": "edit", "reason": "why this treatment", "keep_seconds": null},
   {"action": "remove", "shot_id": "s013", "reason": "..."},
   {"action": "new", "after": "s013", "description": "...", "characters": [],
    "dialogue": [], "scene_id": "scene_1", "duration": 4, "reason": "..."}
 ]}
"characters" lists only characters the storyboard does not have yet. In a change, omit
fields that stay the same. Write text in {language}; keep dialogue in its original language
unless the instruction asks otherwise."""


def compact(doc: StoryboardDoc) -> dict[str, Any]:
    """What the model needs to know, without file paths or bookkeeping."""
    return {
        "characters": [
            {"character_id": c.character_id, "name": c.name, "description": c.description}
            for c in doc.characters
        ],
        "scenes": [{"scene_id": s.scene_id, "description": s.description} for s in doc.scenes],
        "shots": [
            {
                "shot_id": s.shot_id,
                "seconds": round(s.source.end - s.source.start, 2)
                if s.source
                else s.generation.duration,
                "description": s.narrative.description,
                "characters": [a.model_dump(exclude_none=True) for a in s.narrative.characters],
                "dialogue": [
                    d.model_dump(include={"character_id", "text"}, exclude_none=True)
                    for d in s.narrative.dialogue
                ],
                "scene_id": s.assets.scene_id,
                "shot_size": s.visual.shot_size,
                "status": s.edit.treatment.value,
            }
            for s in doc.shots
        ],
    }


def build_request(doc: StoryboardDoc, instruction: str, language: str) -> dict[str, Any]:
    context = compact(doc)
    return {
        "system": SYSTEM.replace("{language}", LANGUAGE_NAMES.get(language, "English")),
        "prompt": (
            f"Storyboard:\n{json.dumps(context, ensure_ascii=False)}\n\nInstruction: {instruction}"
        ),
        "json_output": True,
        "task": PURPOSE,
        "context": {"instruction": instruction, **context},
    }


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------


class PlotEditOptions(BaseModel):
    instruction: str = Field(min_length=1, max_length=MAX_INSTRUCTION)
    language: Literal["en", "zh-CN"] = "en"


def start_plot_edit(session: Session, project_id: str, options: PlotEditOptions) -> Job:
    instruction = options.instruction.strip()
    if not instruction:
        raise PlotEditError("Describe the change you want")
    try:
        doc = latest_doc(session, project_id)
        selection = selection_for(session, Capability.LLM_TEXT)
    except VersionError as exc:
        raise PlotEditError(str(exc), error_type=exc.error_type) from exc
    except CapabilityUnavailable as exc:
        raise PlotEditError(str(exc), error_type="not_configured") from exc
    request = build_request(doc, instruction, options.language)
    return enqueue_job(
        session,
        capability=Capability.LLM_TEXT,
        provider=selection.provider or "",
        model=selection.model,
        project_id=project_id,
        purpose=PURPOSE,
        request={**request, "base_version": doc.version, "instruction": instruction},
    )


# ---------------------------------------------------------------------------
# Proposal
# ---------------------------------------------------------------------------


class ShotText(BaseModel):
    description: str = ""
    dialogue: list[str] = Field(default_factory=list)


class ProposedChange(BaseModel):
    key: str  # what the user accepts: "change:s012", "remove:s013", "new:0"
    kind: Literal["change", "remove", "new"]
    shot_id: str
    after: str | None = None
    before: ShotText | None = None
    proposed: ShotText | None = None
    treatment: Treatment
    reason: str | None = None


class Proposal(BaseModel):
    job_id: str
    status: JobStatus
    instruction: str
    base_version: int | None
    summary: str = ""
    changes: list[ProposedChange] = Field(default_factory=list)
    error_type: str | None = None
    error: str | None = None


def _text(shot: Shot) -> ShotText:
    return ShotText(
        description=shot.narrative.description,
        dialogue=[line.text for line in shot.narrative.dialogue],
    )


def _new_id(after: str | None, taken: set[str]) -> str:
    stem = after or "s000"
    for suffix in "abcdefghijklmnopqrstuvwxyz":
        candidate = f"{stem}{suffix}"
        if candidate not in taken:
            return candidate
    raise PlotEditError("Too many new shots in one place")


def _narrative_treatment(value: str | None) -> Treatment:
    try:
        treatment = Treatment(value) if value else Treatment.REGENERATE
    except ValueError:
        return Treatment.REGENERATE
    return treatment if treatment in NARRATIVE_TREATMENTS else Treatment.REGENERATE


def _changed_shot(shot: Shot, change: ChangeIn, characters: set[str], scenes: set[str]) -> Shot:
    narrative = shot.narrative
    updates: dict[str, Any] = {}
    if change.description is not None:
        updates["description"] = change.description
    if change.characters is not None:
        updates["characters"] = [
            CharacterAction(**a.model_dump())
            for a in change.characters
            if a.character_id in characters
        ]
    if change.dialogue is not None:
        updates["dialogue"] = [
            DialogueLine(
                character_id=line.character_id if line.character_id in characters else None,
                text=line.text,
            )
            for line in change.dialogue
            if line.text.strip()
        ]
    new_narrative = narrative.model_copy(update=updates)
    assets = shot.assets
    if change.characters is not None:
        assets = assets.model_copy(
            update={
                "character_ids": list(
                    dict.fromkeys(a.character_id for a in new_narrative.characters)
                )
            }
        )
    if change.scene_id in scenes:
        assets = assets.model_copy(update={"scene_id": change.scene_id})
    treatment = _narrative_treatment(change.treatment)
    trim = None
    if treatment == Treatment.TRIM and shot.source and change.keep_seconds:
        keep = min(max(change.keep_seconds, 0.2), shot.source.end - shot.source.start)
        trim = Trim(keep_start=shot.source.start, keep_end=round(shot.source.start + keep, 3))
    elif treatment == Treatment.TRIM:
        treatment = Treatment.REGENERATE  # a trim without a length cannot be cut
    return shot.model_copy(
        update={
            "narrative": new_narrative,
            "assets": assets,
            "edit": ShotEdit(
                treatment=treatment,
                treatment_reason=change.reason,
                trim=trim,
                instruction=change.description,
            ),
        }
    )


def _new_shot(shot_id: str, change: ChangeIn, characters: set[str], scenes: set[str]) -> Shot:
    actions = [
        CharacterAction(**a.model_dump())
        for a in change.characters or []
        if a.character_id in characters
    ]
    return Shot(
        shot_id=shot_id,
        source=None,
        narrative=Narrative(
            description=change.description or "",
            characters=actions,
            dialogue=[
                DialogueLine(
                    character_id=d.character_id if d.character_id in characters else None,
                    text=d.text,
                )
                for d in change.dialogue or []
                if d.text.strip()
            ],
        ),
        assets=ShotAssets(
            scene_id=change.scene_id if change.scene_id in scenes else None,
            character_ids=list(dict.fromkeys(a.character_id for a in actions)),
        ),
        edit=ShotEdit(treatment=Treatment.NEW, treatment_reason=change.reason),
        generation=ShotGeneration(
            duration=min(max(change.duration or DEFAULT_NEW_SHOT_S, 1.0), 15.0)
        ),
    )


def apply_changes(
    doc: StoryboardDoc, reply: PlotEditReply, accepted: set[str] | None = None
) -> tuple[StoryboardDoc, list[ProposedChange]]:
    """Apply the reply's changes (all, or only the ``accepted`` keys) to ``doc``.

    Returns the new document and every change that could be applied, so the same code
    builds the preview and the saved version.
    """
    known = {c.character_id for c in doc.characters}
    new_characters = [
        Character(
            character_id=c.character_id, name=c.name or c.character_id, description=c.description
        )
        for c in reply.characters
        if c.character_id not in known
    ]
    characters = known | {c.character_id for c in new_characters}
    scenes = {s.scene_id for s in doc.scenes}
    shots = list(doc.shots)
    taken = {s.shot_id for s in shots}
    applied: list[ProposedChange] = []

    def wanted(key: str) -> bool:
        return accepted is None or key in accepted

    for index, change in enumerate(reply.changes):
        position = next((i for i, s in enumerate(shots) if s.shot_id == change.shot_id), None)
        if change.action == "new":
            anchor = next((i for i, s in enumerate(shots) if s.shot_id == change.after), None)
            shot_id = _new_id(change.after if anchor is not None else None, taken)
            shot = _new_shot(shot_id, change, characters, scenes)
            key = f"new:{index}"
            applied.append(
                ProposedChange(
                    key=key,
                    kind="new",
                    shot_id=shot_id,
                    after=change.after if anchor is not None else None,
                    proposed=_text(shot),
                    treatment=Treatment.NEW,
                    reason=change.reason,
                )
            )
            if wanted(key):
                taken.add(shot_id)
                shots.insert(0 if anchor is None else anchor + 1, shot)
            continue
        if position is None:
            continue  # the model referred to a shot that does not exist
        original = shots[position]
        if change.action == "remove":
            key = f"remove:{original.shot_id}"
            applied.append(
                ProposedChange(
                    key=key,
                    kind="remove",
                    shot_id=original.shot_id,
                    before=_text(original),
                    treatment=Treatment.REMOVED,
                    reason=change.reason,
                )
            )
            if wanted(key):
                if original.source is None:
                    shots.pop(position)
                else:
                    shots[position] = original.model_copy(
                        update={
                            "edit": ShotEdit(
                                treatment=Treatment.REMOVED,
                                treatment_locked=True,
                                treatment_reason=change.reason,
                            )
                        }
                    )
            continue
        changed = _changed_shot(original, change, characters, scenes)
        key = f"change:{original.shot_id}"
        applied.append(
            ProposedChange(
                key=key,
                kind="change",
                shot_id=original.shot_id,
                before=_text(original),
                proposed=_text(changed),
                treatment=changed.edit.treatment,
                reason=change.reason,
            )
        )
        if wanted(key):
            shots[position] = changed
    used = {cid for s in shots for cid in s.assets.character_ids}
    added = [c for c in new_characters if c.character_id in used]
    updated = doc.model_copy(update={"characters": [*doc.characters, *added], "shots": shots})
    return StoryboardDoc.model_validate(updated.model_dump(mode="json")), applied


def _reply(job: Job) -> PlotEditReply:
    data = (job.result or {}).get("data") or {}
    return parse_reply(str(data.get("text", "")))


def proposal_for(session: Session, job: Job) -> Proposal:
    base = Proposal(
        job_id=job.id,
        status=job.status,
        instruction=str(job.request.get("instruction", "")),
        base_version=job.request.get("base_version"),
        error_type=job.error_type,
        error=job.error,
    )
    if job.status != JobStatus.SUCCEEDED or job.project_id is None:
        return base
    try:
        reply = _reply(job)
        doc = latest_doc(session, job.project_id)
        _, changes = apply_changes(doc, reply)
    except (PlotEditError, VersionError, ValidationError) as exc:
        error_type = getattr(exc, "error_type", "parse_error")
        return base.model_copy(
            update={"status": JobStatus.FAILED, "error_type": error_type, "error": str(exc)}
        )
    return base.model_copy(update={"summary": reply.summary, "changes": changes})


def apply_proposal(
    session: Session, job: Job, accepted: list[str]
) -> tuple[StoryboardDoc, list[str]]:
    if job.purpose != PURPOSE or job.project_id is None:
        raise PlotEditError("Not a plot edit", error_type="not_found")
    if job.status != JobStatus.SUCCEEDED:
        raise PlotEditError("This plot edit has not finished", error_type="conflict")
    if job.result and job.result.get("applied_version"):
        raise PlotEditError("This plot edit was already applied", error_type="conflict")
    if not accepted:
        raise PlotEditError("Choose at least one change to apply")
    reply = _reply(job)
    doc = latest_doc(session, job.project_id)
    updated, _ = apply_changes(doc, reply, set(accepted))
    note = str(job.request.get("instruction", ""))[:200] or None
    saved, changed = save_version(
        session, job.project_id, updated, source=StoryboardSource.LLM_EDIT, note=note
    )
    job.result = {**(job.result or {}), "applied_version": saved.version}
    session.add(job)
    session.commit()
    return saved, changed
