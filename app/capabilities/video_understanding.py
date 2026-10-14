"""Provider-neutral contract for ``video_understanding`` (docs/PLAN.md §7 step 2).

The orchestrator detects shots locally, so timestamps never come from the model. Each
request lists the shots with a few sampled frames; the model only describes them and
links recurring characters and scenes. Adapters turn :class:`UnderstandingRequest` into
their own payload and parse the reply with :func:`parse_understanding`.
"""

import json
import re
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, Field, ValidationError, field_validator

from app.capabilities.base import InputInvalid

# Shots per provider call. Larger videos are described in several calls; characters and
# scenes found so far are passed along so ids stay consistent. 40 shots in one call ran
# past two minutes and was dropped by the provider; 12 keeps each call well under that.
CHUNK_SIZE = 12

SHOT_SIZES = (
    "extreme_wide",
    "wide",
    "full",
    "medium",
    "medium_close_up",
    "close_up",
    "extreme_close_up",
    "insert",
)
CAMERA_MOTIONS = (
    "static",
    "pan_left",
    "pan_right",
    "tilt_up",
    "tilt_down",
    "push_in",
    "pull_out",
    "zoom_in",
    "zoom_out",
    "tracking",
    "orbit",
    "crane",
    "handheld",
)

LANGUAGE_NAMES = {"en": "English", "zh-CN": "Simplified Chinese"}


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------


class ShotInput(BaseModel):
    shot_id: str
    start: float
    end: float
    frames: list[str] = Field(min_length=1)  # local image paths, in time order


class KnownCharacter(BaseModel):
    character_id: str
    name: str
    description: str = ""


class KnownScene(BaseModel):
    scene_id: str
    description: str = ""


class UnderstandingRequest(BaseModel):
    shots: list[ShotInput] = Field(min_length=1)
    language: str = "en"
    characters: list[KnownCharacter] = Field(default_factory=list)
    scenes: list[KnownScene] = Field(default_factory=list)


def parse_request(raw: dict[str, Any], *, provider: str) -> UnderstandingRequest:
    try:
        return UnderstandingRequest.model_validate(raw)
    except ValidationError as exc:
        raise InputInvalid(
            f"Bad video understanding request: {exc.errors()[0]['msg']}", provider=provider
        ) from exc


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


def _snake(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return cleaned or None


# Models occasionally emit null, numbers or nested objects where text is expected.
Text = Annotated[str, BeforeValidator(lambda v: v if isinstance(v, str) else "")]
OptText = Annotated[str | None, BeforeValidator(lambda v: v if isinstance(v, str) else None)]
Id = Annotated[str, BeforeValidator(lambda v: str(v) if isinstance(v, (str, int)) else v)]
OptId = Annotated[
    str | None, BeforeValidator(lambda v: str(v) if isinstance(v, (str, int)) and v != "" else None)
]


class CharacterOut(BaseModel):
    character_id: Id
    name: Text = ""
    description: Text = ""


class SceneOut(BaseModel):
    scene_id: Id
    description: Text = ""


class ActionOut(BaseModel):
    character_id: Id
    action: OptText = None
    expression: OptText = None


class LineOut(BaseModel):
    character_id: OptId = None
    text: Text


class VisualOut(BaseModel):
    shot_size: OptText = None
    camera_motion: OptText = None
    composition: OptText = None
    lighting: OptText = None

    @field_validator("shot_size", "camera_motion")
    @classmethod
    def _normalise(cls, value: str | None) -> str | None:
        return _snake(value)


def _as_items(value: Any, key: str, required: str) -> list[dict[str, Any]]:
    """Coerce a model's list loosely: bare strings become ``{key: s}``; items without
    ``required`` are dropped rather than failing the whole storyboard."""
    if not isinstance(value, list):
        return []
    items = [
        {key: v} if isinstance(v, str) else v.model_dump() if isinstance(v, BaseModel) else v
        for v in value
    ]
    return [i for i in items if isinstance(i, dict) and i.get(required)]


class ShotOut(BaseModel):
    shot_id: Id
    description: Text = ""
    characters: list[ActionOut] = Field(default_factory=list)
    dialogue: list[LineOut] = Field(default_factory=list)
    visual: VisualOut = Field(default_factory=VisualOut)
    scene_id: OptId = None

    @field_validator("characters", mode="before")
    @classmethod
    def _loose_characters(cls, value: Any) -> list[dict[str, Any]]:
        return _as_items(value, "character_id", "character_id")

    @field_validator("dialogue", mode="before")
    @classmethod
    def _loose_dialogue(cls, value: Any) -> list[dict[str, Any]]:
        return _as_items(value, "text", "text")

    @field_validator("visual", mode="before")
    @classmethod
    def _loose_visual(cls, value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}


class UnderstandingResult(BaseModel):
    characters: list[CharacterOut] = Field(default_factory=list)
    scenes: list[SceneOut] = Field(default_factory=list)
    shots: list[ShotOut] = Field(default_factory=list)

    @field_validator("characters", mode="before")
    @classmethod
    def _loose_characters(cls, value: Any) -> list[dict[str, Any]]:
        return _as_items(value, "character_id", "character_id")

    @field_validator("scenes", mode="before")
    @classmethod
    def _loose_scenes(cls, value: Any) -> list[dict[str, Any]]:
        return _as_items(value, "scene_id", "scene_id")

    @field_validator("shots", mode="before")
    @classmethod
    def _loose_shots(cls, value: Any) -> list[dict[str, Any]]:
        return _as_items(value, "shot_id", "shot_id")

    def merged_with(self, later: "UnderstandingResult") -> "UnderstandingResult":
        """Combine chunk results. A later chunk may refine a known character or scene."""
        characters = {c.character_id: c for c in [*self.characters, *later.characters]}
        scenes = {s.scene_id: s for s in [*self.scenes, *later.scenes]}
        return UnderstandingResult(
            characters=list(characters.values()),
            scenes=list(scenes.values()),
            shots=[*self.shots, *later.shots],
        )


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

# Illustrative values, not placeholders: with empty strings in the example, models tend to
# echo empty objects back.
OUTPUT_EXAMPLE = {
    "characters": [
        {
            "character_id": "char_1",
            "name": "Woman in red coat",
            "description": "Young woman, shoulder-length black hair, red wool coat",
        }
    ],
    "scenes": [{"scene_id": "scene_1", "description": "Small kitchen, window on the right"}],
    "shots": [
        {
            "shot_id": "s001",
            "description": "The woman stops cooking and turns to look out of the window.",
            "characters": [
                {
                    "character_id": "char_1",
                    "action": "turns to the window",
                    "expression": "hesitant",
                }
            ],
            "dialogue": [{"character_id": "char_1", "text": "I'm not sure I should go."}],
            "visual": {
                "shot_size": "medium_close_up",
                "camera_motion": "push_in",
                "composition": "woman on the left third, window on the right",
                "lighting": "warm daylight from the window",
            },
            "scene_id": "scene_1",
        }
    ],
}


PROMPT = """\
You are a film editor building a storyboard. The video was already split into shots; each
shot below is given as a short sequence of frames in time order.

For EVERY shot, in the same order and with the same shot_id, describe:
- description: one or two sentences on what happens.
- characters: who is visible, their action and facial expression.
- dialogue: spoken lines visible as on-screen subtitles, copied verbatim in their original
  language. Leave it empty when there are none. Never invent lines.
- visual.shot_size: one of {shot_sizes}.
- visual.camera_motion: one of {camera_motions}.
- visual.composition and visual.lighting: a short phrase each.
- scene_id: the location the shot takes place in.

Give the same person the same character_id in every shot they appear in, and the same place
the same scene_id. Reuse the ids of the known characters and scenes below when they appear
again; create new ids (char_N, scene_N) only for new ones. Name people by their visible role
(e.g. "Presenter", "Audience member in glasses") rather than guessing who they are.

Write description, name, action, expression, composition and lighting in {language}.

Known so far: {known}

Reply with one JSON object only, in exactly this shape (the values are only an example):
{shape}"""


def instructions(request: UnderstandingRequest) -> str:
    known = {
        "characters": [c.model_dump() for c in request.characters],
        "scenes": [s.model_dump() for s in request.scenes],
    }
    return PROMPT.format(
        shot_sizes=", ".join(SHOT_SIZES),
        camera_motions=", ".join(CAMERA_MOTIONS),
        language=LANGUAGE_NAMES.get(request.language, "English"),
        known=json.dumps(known, ensure_ascii=False),
        shape=json.dumps(OUTPUT_EXAMPLE, ensure_ascii=False),
    )


def shot_label(shot: ShotInput) -> str:
    return f"Shot {shot.shot_id} ({shot.start:.2f}s to {shot.end:.2f}s):"


# ---------------------------------------------------------------------------
# Parsing and chunking
# ---------------------------------------------------------------------------

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def parse_understanding(
    text: str, *, provider: str, expected: set[str] | None = None
) -> UnderstandingResult:
    """Parse a model reply. Raises :class:`InputInvalid` when it is not the expected JSON,
    or when ``expected`` is given and the reply skipped any of those shot ids."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        raw: Any = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise InputInvalid(f"Model reply is not valid JSON: {exc.msg}", provider=provider) from exc
    # Some replies wrap the object, e.g. {"storyboard": {...}}.
    if isinstance(raw, dict) and "shots" not in raw and len(raw) == 1:
        (inner,) = raw.values()
        raw = inner if isinstance(inner, dict) else raw
    try:
        result = UnderstandingResult.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"])
        raise InputInvalid(
            f"Model reply does not match the storyboard shape at {where}: {first['msg']}",
            provider=provider,
        ) from exc
    if not result.shots:
        raise InputInvalid("Model reply describes no shots", provider=provider)
    missing = (expected or set()) - {shot.shot_id for shot in result.shots}
    if missing:
        raise InputInvalid(f"Model reply skipped shots {sorted(missing)}", provider=provider)
    return result


def chunks(request: UnderstandingRequest, size: int = CHUNK_SIZE) -> list[list[ShotInput]]:
    return [request.shots[i : i + size] for i in range(0, len(request.shots), size)]


async def parse_with_retry(
    fetch_text: Callable[[], Awaitable[str]],
    *,
    provider: str,
    expected: set[str],
    attempts: int = 2,
) -> UnderstandingResult:
    """Ask again once when the reply is not valid storyboard JSON (docs/PLAN.md §7).

    Earlier attempts also require every expected shot; the last one accepts a partial
    reply, leaving skipped shots with empty descriptions.
    """
    for attempt in range(1, attempts + 1):
        strict = attempt < attempts
        try:
            return parse_understanding(
                await fetch_text(), provider=provider, expected=expected if strict else None
            )
        except InputInvalid:
            if attempt == attempts:
                raise
    raise InputInvalid("No usable reply", provider=provider)


async def describe_in_chunks(
    request: UnderstandingRequest,
    call: Callable[[UnderstandingRequest], Awaitable[UnderstandingResult]],
    size: int = CHUNK_SIZE,
) -> UnderstandingResult:
    """Run ``call`` per chunk, feeding each chunk the characters and scenes found so far."""
    result = UnderstandingResult(
        characters=[CharacterOut(**c.model_dump()) for c in request.characters],
        scenes=[SceneOut(**s.model_dump()) for s in request.scenes],
    )
    for part in chunks(request, size):
        sub_request = request.model_copy(
            update={
                "shots": part,
                "characters": [KnownCharacter(**c.model_dump()) for c in result.characters],
                "scenes": [KnownScene(**s.model_dump()) for s in result.scenes],
            }
        )
        result = result.merged_with(await call(sub_request))
    return result
