import asyncio
import json
import subprocess

import pytest
from sqlmodel import Session

from app.core.db import get_engine
from app.models import Storyboard, StoryboardSource
from app.pipeline import assets, parse, plot_edit, reparse
from app.pipeline.worker import Worker
from app.storyboard.diff import affected_shots, changed_assets, refresh_edits
from app.storyboard.schema import CharacterAction, Layer, StoryboardDoc, Treatment
from tests.test_parse import _configure, _project, _upload


def _doc(**overrides) -> StoryboardDoc:
    base = {
        "project_id": "p",
        "version": 1,
        "characters": [
            {"character_id": "char_a", "name": "A", "description": "tall"},
            {"character_id": "char_b", "name": "B"},
        ],
        "scenes": [{"scene_id": "scene_1", "description": "kitchen"}],
        "shots": [
            {
                "shot_id": f"s00{i}",
                "source": {"clip": f"clips/s00{i}.mp4", "start": i, "end": i + 2},
                "narrative": {"description": f"shot {i}"},
                "assets": {"scene_id": "scene_1", "character_ids": [cid]},
            }
            for i, cid in ((1, "char_a"), (2, "char_b"), (3, "char_a"))
        ],
    }
    return StoryboardDoc.model_validate({**base, **overrides})


def _treatments(doc: StoryboardDoc) -> dict[str, str]:
    return {s.shot_id: s.edit.treatment.value for s in doc.shots}


# -- diff ----------------------------------------------------------------------------


def test_new_look_for_a_character_swaps_every_shot_it_appears_in():
    base = _doc()
    edited = base.model_copy(deep=True)
    edited.characters[0].reference_images = ["assets/char_a_1.png"]
    assert changed_assets(base, edited) == {"char_a"}
    refreshed = refresh_edits(base, edited)
    assert _treatments(refreshed) == {"s001": "swap", "s002": "keep", "s003": "swap"}
    assert refreshed.shots[0].edit.changed_layers == [Layer.ASSETS]


def test_adding_a_bystander_is_an_assets_change():
    base = _doc()
    edited = base.model_copy(deep=True)
    edited.characters[1].reference_images = ["assets/char_b_1.png"]
    edited.shots[0].assets.character_ids.append("char_b")
    edited.shots[0].narrative.characters.append(
        CharacterAction(character_id="char_b", action=None, expression=None)
    )
    refreshed = refresh_edits(base, edited)
    assert refreshed.shots[0].edit.changed_layers == [Layer.ASSETS]
    assert refreshed.shots[0].edit.treatment == Treatment.SWAP


def test_renaming_a_character_changes_nothing():
    base = _doc()
    edited = base.model_copy(deep=True)
    edited.characters[0].name = "Alice"
    assert _treatments(refresh_edits(base, edited)) == {
        "s001": "keep",
        "s002": "keep",
        "s003": "keep",
    }


def test_narrative_edits_use_the_suggestion_or_regenerate():
    base = _doc()
    edited = base.model_copy(deep=True)
    edited.shots[0].narrative.description = "A smiles"
    edited.shots[0].edit.treatment = Treatment.EDIT  # llm_text's suggestion
    edited.shots[1].narrative.description = "B leaves"
    edited.shots[1].edit.treatment = Treatment.SWAP  # not a narrative treatment
    edited.shots[2].visual.lighting = "night"
    refreshed = refresh_edits(base, edited)
    assert _treatments(refreshed) == {"s001": "edit", "s002": "regenerate", "s003": "edit"}
    assert refreshed.shots[2].edit.changed_layers == [Layer.VISUAL]


def test_locked_removed_and_new_shots():
    base = _doc()
    edited = base.model_copy(deep=True)
    edited.shots[0].edit.treatment = Treatment.REGENERATE
    edited.shots[0].edit.treatment_locked = True
    edited.shots[1].edit.treatment = Treatment.REMOVED
    data = edited.model_dump(mode="json")
    data["shots"].append(
        {
            "shot_id": "s003a",
            "source": None,
            "edit": {"treatment": "new"},
            "generation": {"duration": 3},
        }
    )
    refreshed = refresh_edits(base, StoryboardDoc.model_validate(data))
    assert _treatments(refreshed) == {
        "s001": "regenerate",
        "s002": "removed",
        "s003": "keep",
        "s003a": "new",
    }
    # An unchanged shot drops back to keep once it is unlocked.
    reverted = refreshed.model_copy(deep=True)
    reverted.shots[0].edit.treatment_locked = False
    assert _treatments(refresh_edits(base, reverted))["s001"] == "keep"
    assert affected_shots(base, refreshed) == ["s001", "s002", "s003a"]


# -- plot edit replies ---------------------------------------------------------------


REPLY = {
    "summary": "A stays home",
    "characters": [{"character_id": "char_c", "name": "Cat", "description": "a grey cat"}],
    "changes": [
        {
            "action": "change",
            "shot_id": "s003",
            "description": "A sits back down",
            "characters": [{"character_id": "char_a", "action": "sits"}, {"character_id": "ghost"}],
            "dialogue": [{"character_id": "char_a", "text": "Not today."}, {"text": " "}],
            "treatment": "motion",
            "reason": "similar movement",
        },
        {"action": "remove", "shot_id": "s002", "reason": "B is gone"},
        {
            "action": "new",
            "after": "s003",
            "description": "The cat jumps up",
            "characters": [{"character_id": "char_c"}],
            "duration": 40,
        },
        {
            "action": "change",
            "shot_id": "s001",
            "treatment": "trim",
            "keep_seconds": 0.5,
            "description": "A pauses",
        },
        {"action": "change", "shot_id": "s404", "description": "no such shot"},
        {"action": "explode", "shot_id": "s001"},
        "not an object",
    ],
}


def test_parse_reply_tolerates_fences_prose_and_bad_items():
    text = "Sure! Here it is:\n```json\n" + json.dumps(REPLY) + "\n```"
    reply = plot_edit.parse_reply(text)
    assert reply.summary == "A stays home"
    assert len(reply.changes) == 5  # the unknown action and the string are dropped
    with pytest.raises(plot_edit.PlotEditError):
        plot_edit.parse_reply("no json here")


def test_apply_changes_all_and_selected():
    doc = _doc()
    reply = plot_edit.parse_reply(json.dumps(REPLY))
    updated, changes = plot_edit.apply_changes(doc, reply)
    assert [c.key for c in changes] == ["change:s003", "remove:s002", "new:2", "change:s001"]
    shots = {s.shot_id: s for s in updated.shots}
    assert [s.shot_id for s in updated.shots] == ["s001", "s002", "s003", "s003a"]
    assert shots["s003"].narrative.description == "A sits back down"
    assert [a.character_id for a in shots["s003"].narrative.characters] == ["char_a"]
    assert [d.text for d in shots["s003"].narrative.dialogue] == ["Not today."]
    assert shots["s003"].edit.treatment == Treatment.MOTION
    assert shots["s002"].edit.treatment == Treatment.REMOVED
    assert shots["s003a"].edit.treatment == Treatment.NEW
    assert shots["s003a"].generation.duration == 15.0
    assert shots["s001"].edit.trim.keep_end == pytest.approx(1.5)
    assert "char_c" in {c.character_id for c in updated.characters}

    only_one, _ = plot_edit.apply_changes(doc, reply, {"remove:s002"})
    assert _treatments(only_one)["s002"] == "removed"
    assert _treatments(only_one)["s003"] == "keep"
    assert "char_c" not in {c.character_id for c in only_one.characters}


# -- API -----------------------------------------------------------------------------


def _worker() -> Worker:
    return Worker(
        get_engine(),
        hooks=[parse.on_job_finished, assets.on_job_finished, reparse.on_job_finished],
    )


def _drain(times: int = 4) -> None:
    worker = _worker()
    for _ in range(times):
        asyncio.run(worker.run_once())


@pytest.fixture
def parsed(client, sample_video) -> str:
    _configure(client)
    project_id = _project(client)
    _upload(client, project_id, sample_video)
    client.post(f"/api/projects/{project_id}/parse")
    _drain()
    assert client.get(f"/api/projects/{project_id}").json()["status"] == "ready"
    return project_id


def test_edit_restore_and_history(client, parsed):
    url = f"/api/projects/{parsed}/storyboard"
    doc = client.get(url).json()
    doc["shots"][1]["narrative"]["description"] = "Edited by hand"
    saved = client.put(url, json={"storyboard": doc, "base_version": 1, "note": "hand edit"})
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["changed"] == ["s002"]
    assert body["storyboard"]["version"] == 2
    assert body["storyboard"]["shots"][1]["edit"]["treatment"] == "regenerate"

    stale = client.put(url, json={"storyboard": doc, "base_version": 1})
    assert stale.status_code == 409
    broken = {**doc, "shots": [{**doc["shots"][0], "assets": {"character_ids": ["nobody"]}}]}
    assert client.put(url, json={"storyboard": broken, "base_version": 2}).status_code == 422

    assert (
        client.get(url, params={"version": 1})
        .json()["shots"][1]["narrative"]["description"]
        .startswith("Mock")
    )
    assert client.get(url, params={"version": 9}).status_code == 404
    restored = client.post(f"{url}/restore", json={"version": 1}).json()
    assert restored["version"] == 3 and restored["shots"][1]["edit"]["treatment"] == "keep"
    assert client.post(f"{url}/restore", json={"version": 9}).status_code == 404

    versions = client.get(f"{url}/versions").json()
    assert [(v["version"], v["source"], v["note"]) for v in versions] == [
        (3, "user_edit", "Restored version 1"),
        (2, "user_edit", "hand edit"),
        (1, "auto_parse", None),
    ]


def test_reference_images_upload_generate_and_remove(client, parsed, tmp_path):
    base = f"/api/projects/{parsed}/assets/char_1"
    png = tmp_path / "look.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64",
            "-frames:v",
            "1",
            str(png),
        ],
        check=True,
    )
    with png.open("rb") as handle:
        uploaded = client.post(f"{base}/images", files={"file": ("look.png", handle, "image/png")})
    assert uploaded.status_code == 200, uploaded.text
    doc = uploaded.json()
    image = doc["characters"][0]["reference_images"][0]
    assert image == "assets/char_1_1.png"
    assert {s["edit"]["treatment"] for s in doc["shots"]} == {"swap"}
    assert client.get(f"/api/projects/{parsed}/files/{image}").status_code == 200

    with png.open("rb") as handle:
        bad = client.post(f"{base}/images", files={"file": ("look.gif", handle, "image/gif")})
    assert bad.status_code == 422
    missing = client.post(
        f"/api/projects/{parsed}/assets/nobody/images/remove", json={"path": image}
    )
    assert missing.status_code == 404

    started = client.post(f"{base}/generate", json={"prompt": "as a cartoon sheep"})
    assert started.status_code == 202, started.text
    job_id = started.json()["job_id"]
    _drain()
    candidates = client.get(f"/api/projects/{parsed}/assets/candidates").json()
    candidate = next(c for c in candidates if c["job_id"] == job_id)
    assert candidate["status"] == "succeeded" and candidate["prompt"] == "as a cartoon sheep"
    assert candidate["image"].startswith("assets/generated/")
    used = client.post(f"/api/projects/{parsed}/assets/candidates/use", json={"job_id": job_id})
    assert used.json()["characters"][0]["reference_images"][0] == candidate["image"]
    assert (
        client.post(
            f"/api/projects/{parsed}/assets/candidates/use", json={"job_id": "x"}
        ).status_code
        == 404
    )

    removed = client.post(f"{base}/images/remove", json={"path": image}).json()
    assert removed["characters"][0]["reference_images"] == [candidate["image"]]
    assert client.post(f"{base}/images/remove", json={"path": image}).status_code == 404


def test_plot_edit_review_and_apply(client, parsed):
    base = f"/api/projects/{parsed}/plot-edits"
    started = client.post(base, json={"instruction": "A never leaves", "language": "en"})
    assert started.status_code == 202, started.text
    assert started.json()["status"] == "queued"
    job_id = started.json()["job_id"]
    assert client.post(f"{base}/{job_id}/apply", json={"keys": ["x"]}).status_code == 409

    _drain()
    proposal = client.get(f"{base}/{job_id}").json()
    assert proposal["status"] == "succeeded"
    assert proposal["base_version"] == 1 and proposal["instruction"] == "A never leaves"
    kinds = {c["key"]: c["treatment"] for c in proposal["changes"]}
    assert kinds == {"remove:s002": "removed", "change:s003": "regenerate", "new:2": "new"}
    change = next(c for c in proposal["changes"] if c["kind"] == "change")
    assert change["before"]["description"].startswith("Mock description")
    assert "A never leaves" in change["proposed"]["description"]

    applied = client.post(f"{base}/{job_id}/apply", json={"keys": ["change:s003", "new:2"]})
    assert applied.status_code == 200, applied.text
    doc = applied.json()["storyboard"]
    assert [s["shot_id"] for s in doc["shots"]] == ["s001", "s002", "s003", "s003a"]
    assert {s["shot_id"]: s["edit"]["treatment"] for s in doc["shots"]} == {
        "s001": "keep",
        "s002": "keep",
        "s003": "regenerate",
        "s003a": "new",
    }
    assert client.post(f"{base}/{job_id}/apply", json={"keys": ["new:2"]}).status_code == 409
    versions = client.get(f"/api/projects/{parsed}/storyboard/versions").json()
    assert versions[0]["source"] == "llm_edit" and versions[0]["note"] == "A never leaves"
    assert client.get(f"{base}/missing").status_code == 404


def test_plot_edit_needs_a_storyboard_and_settings(client, sample_video):
    project_id = _project(client)
    response = client.post(f"/api/projects/{project_id}/plot-edits", json={"instruction": "x"})
    assert response.status_code == 404
    assert (
        client.post(f"/api/projects/{project_id}/plot-edits", json={"instruction": ""}).status_code
        == 422
    )
    with Session(get_engine()) as session:
        session.add(
            Storyboard(
                project_id=project_id,
                version=1,
                content=_doc(project_id=project_id).model_dump(mode="json"),
                source=StoryboardSource.AUTO_PARSE,
            )
        )
        session.commit()
    no_settings = client.post(f"/api/projects/{project_id}/plot-edits", json={"instruction": "x"})
    assert no_settings.status_code == 409


def test_describe_one_shot_again(client, parsed):
    url = f"/api/projects/{parsed}/storyboard"
    doc = client.get(url).json()
    doc["shots"][1]["narrative"]["description"] = "A wrong description"
    doc["characters"][0]["reference_images"] = []
    client.put(url, json={"storyboard": doc, "base_version": 1})
    assert client.get(url).json()["shots"][1]["edit"]["treatment"] == "regenerate"

    started = client.post(f"/api/projects/{parsed}/shots/s002/describe", json={"language": "en"})
    assert started.status_code == 202, started.text
    _drain()
    latest = client.get(url).json()
    shot = latest["shots"][1]
    assert shot["narrative"]["description"] == "Mock description of shot s002"
    assert shot["edit"]["treatment"] == "keep"  # a correction, not an edit
    versions = client.get(f"{url}/versions").json()
    assert [(v["source"], v["note"]) for v in versions[:2]] == [
        ("user_edit", "Described s002 again"),
        ("auto_parse", "Described s002 again"),
    ]
    assert client.post(f"/api/projects/{parsed}/shots/s404/describe", json={}).status_code == 404
