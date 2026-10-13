"""Layered storyboard schema (docs/PLAN.md §6).

A shot = source clip (source of truth) + layered description (editable index) + asset refs.
"""

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class Layer(StrEnum):
    NARRATIVE = "narrative"
    VISUAL = "visual"
    CONTROL = "control"
    ASSETS = "assets"


class Treatment(StrEnum):
    """Lightest first. ``new`` and ``removed`` sit outside the spectrum."""

    KEEP = "keep"
    TRIM = "trim"
    SWAP = "swap"
    EDIT = "edit"
    MOTION = "motion"
    REGENERATE = "regenerate"
    NEW = "new"
    REMOVED = "removed"


class ShotSource(BaseModel):
    clip: str | None = None
    start: float = Field(ge=0)
    end: float = Field(ge=0)
    keyframes: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _end_after_start(self) -> "ShotSource":
        if self.end < self.start:
            raise ValueError("source.end must be >= source.start")
        return self


class CharacterAction(BaseModel):
    character_id: str
    action: str | None = None
    expression: str | None = None


class DialogueLine(BaseModel):
    character_id: str | None = None
    text: str
    start: float | None = None
    end: float | None = None


class Narrative(BaseModel):
    description: str = ""
    characters: list[CharacterAction] = Field(default_factory=list)
    dialogue: list[DialogueLine] = Field(default_factory=list)
    beats: list[dict[str, Any]] = Field(default_factory=list)


class Visual(BaseModel):
    shot_size: str | None = None
    camera_motion: str | None = None
    composition: str | None = None
    lighting: str | None = None


class Control(BaseModel):
    """Extracted on demand. ``None`` means not extracted yet."""

    masks: Any | None = None
    pose: Any | None = None
    camera_track: Any | None = None


class ShotAssets(BaseModel):
    scene_id: str | None = None
    character_ids: list[str] = Field(default_factory=list)
    prop_ids: list[str] = Field(default_factory=list)


class Trim(BaseModel):
    keep_start: float
    keep_end: float


class ShotEdit(BaseModel):
    changed_layers: list[Layer] = Field(default_factory=list)
    treatment: Treatment = Treatment.KEEP
    treatment_locked: bool = False
    treatment_reason: str | None = None
    trim: Trim | None = None
    instruction: str | None = None


class ShotGeneration(BaseModel):
    prompt: str | None = None
    first_frame: str | None = None
    last_frame: str | None = None
    result_clip: str | None = None
    job_ids: list[str] = Field(default_factory=list)


class Shot(BaseModel):
    shot_id: str
    source: ShotSource | None = None  # None only for treatment == "new"
    narrative: Narrative = Field(default_factory=Narrative)
    visual: Visual = Field(default_factory=Visual)
    control: Control = Field(default_factory=Control)
    assets: ShotAssets = Field(default_factory=ShotAssets)
    edit: ShotEdit = Field(default_factory=ShotEdit)
    generation: ShotGeneration = Field(default_factory=ShotGeneration)

    @model_validator(mode="after")
    def _source_required_unless_new(self) -> "Shot":
        if self.source is None and self.edit.treatment != Treatment.NEW:
            raise ValueError(f"shot {self.shot_id}: source is required unless treatment is 'new'")
        return self


class Character(BaseModel):
    character_id: str
    name: str
    description: str = ""
    reference_images: list[str] = Field(default_factory=list)
    voice: str | None = None


class Scene(BaseModel):
    scene_id: str
    description: str = ""
    reference_images: list[str] = Field(default_factory=list)


class StoryboardDoc(BaseModel):
    project_id: str
    version: int = Field(ge=1)
    characters: list[Character] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)
    shots: list[Shot] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids_and_valid_refs(self) -> "StoryboardDoc":
        shot_ids = [s.shot_id for s in self.shots]
        if len(shot_ids) != len(set(shot_ids)):
            raise ValueError("shot_id values must be unique")
        character_ids = {c.character_id for c in self.characters}
        scene_ids = {s.scene_id for s in self.scenes}
        for shot in self.shots:
            unknown = set(shot.assets.character_ids) - character_ids
            if unknown:
                raise ValueError(f"shot {shot.shot_id}: unknown character ids {sorted(unknown)}")
            if shot.assets.scene_id and shot.assets.scene_id not in scene_ids:
                raise ValueError(f"shot {shot.shot_id}: unknown scene id {shot.assets.scene_id}")
        return self
