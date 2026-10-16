"""Compare a storyboard with its parsed baseline (docs/PLAN.md §7 step 4).

The baseline is the latest auto-parsed version: it matches the clips on disk. Every edit
is measured against it, so ``changed_layers`` and the auto-selected treatment always say
how far a shot has moved from its source clip, however many versions lie in between.
"""

from collections.abc import Iterable

from app.storyboard.schema import (
    Character,
    Layer,
    Scene,
    Shot,
    StoryboardDoc,
    Treatment,
)
from app.storyboard.treatment import select_treatment

# Fields of a character or scene that change how it looks on screen. Renaming one is
# bookkeeping and does not need any shot regenerated.
LOOK_FIELDS = ("description", "reference_images")


def _look(item: Character | Scene | None) -> tuple:
    return tuple(getattr(item, field) for field in LOOK_FIELDS) if item else ()


def changed_assets(base: StoryboardDoc, doc: StoryboardDoc) -> set[str]:
    """IDs of characters and scenes whose look differs from the baseline."""
    base_items = {c.character_id: c for c in base.characters} | {s.scene_id: s for s in base.scenes}
    items = {c.character_id: c for c in doc.characters} | {s.scene_id: s for s in doc.scenes}
    return {key for key, item in items.items() if _look(item) != _look(base_items.get(key))}


def _narrative(shot: Shot) -> dict:
    return shot.narrative.model_dump(mode="json")


def _assets(shot: Shot) -> dict:
    data = shot.assets.model_dump(mode="json")
    return {**data, "character_ids": sorted(data["character_ids"])}


def shot_layers(base: Shot, shot: Shot, swapped: Iterable[str]) -> list[Layer]:
    """Layers that differ between a shot and its baseline. ``swapped`` holds the asset IDs
    whose look changed; a shot that shows one of them has a changed assets layer."""
    layers: list[Layer] = []
    if _narrative(base) != _narrative(shot):
        layers.append(Layer.NARRATIVE)
    if base.visual != shot.visual:
        layers.append(Layer.VISUAL)
    shown = {*shot.assets.character_ids, shot.assets.scene_id}
    if _assets(base) != _assets(shot) or shown & set(swapped):
        layers.append(Layer.ASSETS)
    return layers


def refresh_edits(base: StoryboardDoc, doc: StoryboardDoc) -> StoryboardDoc:
    """Recompute ``changed_layers`` and unlocked treatments for every shot.

    - a shot missing from the baseline is ``new``;
    - ``removed`` and locked treatments are the user's choice and are kept;
    - when the narrative changed, the current treatment (``llm_text``'s judgement) is the
      suggestion; otherwise the rules in :func:`select_treatment` decide.
    """
    base_shots = {s.shot_id: s for s in base.shots}
    swapped = changed_assets(base, doc)
    shots = []
    for shot in doc.shots:
        origin = base_shots.get(shot.shot_id)
        edit = shot.edit
        if origin is None:  # a new shot; to drop one, delete it from the list
            layers = [Layer.NARRATIVE]
            treatment = Treatment.NEW
        else:
            layers = shot_layers(origin, shot, swapped)
            if edit.treatment_locked or edit.treatment == Treatment.REMOVED:
                treatment = edit.treatment
            else:
                treatment = select_treatment(layers, edit.treatment.value)
        reason = edit.treatment_reason if treatment == edit.treatment else None
        shots.append(
            shot.model_copy(
                update={
                    "edit": edit.model_copy(
                        update={
                            "changed_layers": layers,
                            "treatment": treatment,
                            "treatment_reason": reason,
                        }
                    )
                }
            )
        )
    return doc.model_copy(update={"shots": shots})


def affected_shots(before: StoryboardDoc, after: StoryboardDoc) -> list[str]:
    """Shot IDs whose content or treatment differs between two versions, in order."""
    old = {s.shot_id: s.model_dump(mode="json") for s in before.shots}
    ids = [s.shot_id for s in after.shots if old.get(s.shot_id) != s.model_dump(mode="json")]
    gone = [shot_id for shot_id in old if shot_id not in {s.shot_id for s in after.shots}]
    return ids + gone
