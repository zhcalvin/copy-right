"""Auto-select the lightest treatment for a shot (docs/PLAN.md §6)."""

from collections.abc import Iterable

from app.storyboard.schema import Layer, Treatment

# Treatments llm_text may pick when the narrative layer changed.
NARRATIVE_TREATMENTS = frozenset(
    {Treatment.TRIM, Treatment.EDIT, Treatment.MOTION, Treatment.REGENERATE}
)


def select_treatment(
    changed_layers: Iterable[Layer | str],
    llm_suggestion: str | None = None,
) -> Treatment:
    """Pick a treatment from the changed layers.

    ``llm_suggestion`` is ``llm_text``'s judgement and only matters when the narrative
    layer changed. An invalid or missing suggestion falls back to ``regenerate``.
    """
    # The control layer is extracted, never edited, so it never drives a treatment.
    layers = {Layer(layer) for layer in changed_layers} - {Layer.CONTROL}

    if not layers:
        return Treatment.KEEP
    if Layer.NARRATIVE in layers:
        try:
            suggested = Treatment(llm_suggestion) if llm_suggestion else None
        except ValueError:
            suggested = None
        return suggested if suggested in NARRATIVE_TREATMENTS else Treatment.REGENERATE
    if Layer.ASSETS in layers:
        return Treatment.SWAP
    # Visual-only (lighting, framing...) is not covered by the plan's rules; an
    # instruction edit on top of the source clip is the lightest fit.
    return Treatment.EDIT
