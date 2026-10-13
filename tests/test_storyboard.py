import pytest
from pydantic import ValidationError

from app.storyboard.samples import load_sample_storyboard
from app.storyboard.schema import Layer, StoryboardDoc, Treatment
from app.storyboard.treatment import select_treatment


def test_sample_storyboard_is_valid():
    doc = StoryboardDoc.model_validate(load_sample_storyboard())
    assert [s.shot_id for s in doc.shots] == ["s001", "s002", "s003"]
    assert doc.shots[0].edit.treatment == Treatment.KEEP


def test_duplicate_shot_ids_rejected():
    raw = load_sample_storyboard()
    raw["shots"][1]["shot_id"] = "s001"
    with pytest.raises(ValidationError, match="unique"):
        StoryboardDoc.model_validate(raw)


def test_unknown_character_reference_rejected():
    raw = load_sample_storyboard()
    raw["shots"][0]["assets"]["character_ids"] = ["char_ghost"]
    with pytest.raises(ValidationError, match="unknown character"):
        StoryboardDoc.model_validate(raw)


def test_source_end_before_start_rejected():
    raw = load_sample_storyboard()
    raw["shots"][0]["source"]["end"] = -1 + raw["shots"][0]["source"]["start"] + 0.5
    raw["shots"][0]["source"]["start"] = 2.0
    with pytest.raises(ValidationError):
        StoryboardDoc.model_validate(raw)


def test_new_shot_may_omit_source():
    raw = load_sample_storyboard()
    raw["shots"].append({"shot_id": "s004", "source": None, "edit": {"treatment": "new"}})
    StoryboardDoc.model_validate(raw)
    raw["shots"][-1]["edit"]["treatment"] = "regenerate"
    with pytest.raises(ValidationError, match="source is required"):
        StoryboardDoc.model_validate(raw)


@pytest.mark.parametrize(
    ("layers", "suggestion", "expected"),
    [
        ([], None, Treatment.KEEP),
        ([Layer.CONTROL], None, Treatment.KEEP),
        ([Layer.ASSETS], None, Treatment.SWAP),
        ([Layer.ASSETS, Layer.VISUAL], None, Treatment.SWAP),
        ([Layer.VISUAL], None, Treatment.EDIT),
        ([Layer.NARRATIVE], "edit", Treatment.EDIT),
        ([Layer.NARRATIVE], "trim", Treatment.TRIM),
        ([Layer.NARRATIVE], "motion", Treatment.MOTION),
        ([Layer.NARRATIVE, Layer.ASSETS], "regenerate", Treatment.REGENERATE),
        # invalid or out-of-range suggestions fall back to regenerate
        ([Layer.NARRATIVE], None, Treatment.REGENERATE),
        ([Layer.NARRATIVE], "banana", Treatment.REGENERATE),
        ([Layer.NARRATIVE], "keep", Treatment.REGENERATE),
        ([Layer.NARRATIVE], "swap", Treatment.REGENERATE),
    ],
)
def test_select_treatment(layers, suggestion, expected):
    assert select_treatment(layers, suggestion) == expected


def test_select_treatment_accepts_strings():
    assert select_treatment(["assets"]) == Treatment.SWAP
