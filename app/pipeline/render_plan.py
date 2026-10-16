"""What each treatment sends to the providers (docs/PLAN.md §6 treatment table, §7 step 5).

Pure functions: given the storyboard, its parsed baseline and one shot, build the
provider-neutral requests. :mod:`app.pipeline.render` runs them.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.capabilities.base import Capability
from app.providers.registry import ModelOption, Registry
from app.storyboard.diff import changed_assets
from app.storyboard.schema import Character, Scene, Shot, StoryboardDoc

MAX_REFERENCES = 4  # wan2.7-videoedit accepts up to 4 reference images
MAX_FRAME_REFERENCES = 2  # image_edit takes 3 images: up to 2 references + the frame

QUALITY = {"preview": "standard", "final": "pro"}
RESOLUTION = {"preview": "720P", "final": "1080P"}


@dataclass(frozen=True)
class Look:
    """A character or scene with a new look that a shot shows."""

    kind: str  # "character" | "scene"
    name: str
    description: str
    image: Path


def looks_for(base: StoryboardDoc, doc: StoryboardDoc, shot: Shot, root: Path) -> list[Look]:
    swapped = changed_assets(base, doc)
    looks: list[Look] = []
    characters = {c.character_id: c for c in doc.characters}
    for character_id in shot.assets.character_ids:
        character: Character | None = characters.get(character_id)
        if character and character_id in swapped and character.reference_images:
            looks.append(
                Look(
                    "character",
                    character.name,
                    character.description,
                    root / character.reference_images[0],
                )
            )
    scene: Scene | None = next((s for s in doc.scenes if s.scene_id == shot.assets.scene_id), None)
    if scene and scene.scene_id in swapped and scene.reference_images:
        looks.append(
            Look("scene", scene.scene_id, scene.description, root / scene.reference_images[0])
        )
    return looks[:MAX_REFERENCES]


def _mapping(looks: list[Look], offset: int = 1) -> str:
    lines = []
    for index, look in enumerate(looks, start=offset):
        if look.kind == "scene":
            lines.append(f"The location becomes the place in image {index}.")
        else:
            who = f"{look.name} ({look.description})" if look.description else look.name
            lines.append(f"{who} becomes the character in image {index}.")
    return " ".join(lines)


KEEP = (
    "Keep the motion, expressions, timing, camera movement, lighting and everything not "
    "mentioned exactly as in the video. Keep any on-screen text and watermarks unchanged."
)


def swap_prompt(looks: list[Look]) -> str:
    return f"Replace these people or places with the reference images. {_mapping(looks)} {KEEP}"


def edit_prompt(shot: Shot, looks: list[Look]) -> str:
    change = shot.edit.instruction or shot.narrative.description
    mapping = f" {_mapping(looks)}" if looks else ""
    return f"Edit the video so that: {change}.{mapping} {KEEP}"


def video_edit_request(
    clip: Path, shot: Shot, looks: list[Look], mode: str, quality: str
) -> dict[str, Any]:
    if mode == "swap":
        return {
            "video": str(clip),
            "reference_images": [str(looks[0].image)],
            "mode": "swap",
            "quality": QUALITY[quality],
        }
    prompt = (
        edit_prompt(shot, looks)
        if mode == "edit" and shot.edit.treatment.value == "edit"
        else swap_prompt(looks)
    )
    return {
        "video": str(clip),
        "reference_images": [str(look.image) for look in looks],
        "mode": "edit",
        "prompt": prompt,
        "quality": QUALITY[quality],
    }


def motion_request(clip: Path, first_frame: Path, quality: str) -> dict[str, Any]:
    return {
        "video": str(clip),
        "reference_images": [str(first_frame)],
        "mode": "motion",
        "quality": QUALITY[quality],
    }


def frame_request(
    shot: Shot, looks: list[Look], base_frame: Path | None, *, new: bool
) -> dict[str, Any]:
    """First frame for ``motion``, ``regenerate`` and ``new`` shots (image_edit)."""
    refs = [look for look in looks if look.kind == "character"][:MAX_FRAME_REFERENCES]
    images = [str(look.image) for look in refs]
    parts = []
    if base_frame is not None:
        images.append(str(base_frame))
        frame_index = len(images)
        if new:
            parts.append(
                f"Image {frame_index} is a frame from the same film. Draw a new frame in the "
                f"same location, style, lighting and colour grading as image {frame_index}."
            )
        else:
            parts.append(
                f"Image {frame_index} is a frame from the original film. Redraw it as the "
                f"first frame of the new version of this shot, keeping its camera framing, "
                f"lighting and colour grading."
            )
    parts.append(f"The frame shows: {shot.narrative.description}")
    if refs:
        parts.append(_mapping(refs))
    if shot.visual.shot_size:
        parts.append(f"Shot size: {shot.visual.shot_size.replace('_', ' ')}.")
    parts.append("No text, captions or borders.")
    return {"images": images, "prompt": " ".join(parts)[:2000]}


def i2v_request(
    shot: Shot, doc: StoryboardDoc, first_frame: Path, duration: float, quality: str
) -> dict[str, Any]:
    names = {c.character_id: c for c in doc.characters}
    cast = [
        f"{names[a.character_id].name}: {a.action or ''} {a.expression or ''}".strip()
        for a in shot.narrative.characters
        if a.character_id in names
    ]
    camera = shot.visual.camera_motion.replace("_", " ") if shot.visual.camera_motion else None
    prompt = " ".join(
        part
        for part in (
            shot.narrative.description,
            f"Characters: {'; '.join(cast)}." if cast else "",
            f"Camera: {camera}." if camera else "",
            f"Lighting: {shot.visual.lighting}." if shot.visual.lighting else "",
        )
        if part
    )
    return {
        "first_frame": str(first_frame),
        "prompt": prompt[:1500],
        "duration": round(min(max(duration, 0.5), 15.0), 3),
        "resolution": RESOLUTION[quality],
    }


# Request modes to try for each treatment, best first. An instruction edit with reference
# images handles several people and face close-ups, which a one-person swap cannot.
VIDEO_MODES = {"swap": ("edit", "swap"), "edit": ("edit",), "motion": ("motion",)}


@dataclass(frozen=True)
class ModelChoice:
    model: str | None
    mode: str
    min_seconds: float | None = None
    max_seconds: float | None = None


def choose_video_model(
    registry: Registry, provider: str, selected: str | None, treatment: str
) -> ModelChoice | None:
    """The video_edit_v2v model and request mode for a treatment: the selected model if it
    serves one of the treatment's modes, else the provider's best match. Models that
    declare no modes (e.g. the mock) serve every mode. ``None``: nothing fits."""
    wanted = VIDEO_MODES[treatment]
    options = registry.providers[provider].models.get(Capability.VIDEO_EDIT_V2V, [])
    if not options:
        return ModelChoice(model=selected, mode=wanted[0])

    def fit(option: ModelOption, mode: str) -> ModelChoice | None:
        if option.modes and mode not in option.modes:
            return None
        return ModelChoice(option.id, mode, option.min_seconds, option.max_seconds)

    chosen = next((o for o in options if o.id == selected), None)
    if chosen is not None:
        for mode in wanted:
            if choice := fit(chosen, mode):
                return choice
    for mode in wanted:
        for option in options:
            if choice := fit(option, mode):
                return choice
    return None
