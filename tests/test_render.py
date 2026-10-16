import asyncio
import subprocess

import pytest
from sqlmodel import Session

from app.core.db import get_engine
from app.models import Render
from app.pipeline import assets, parse, render, render_plan
from app.pipeline.worker import Worker
from app.providers.registry import get_registry
from app.storage.local import project_dir
from app.tools.media import Segment, normalize_clip, probe, split_points, stitch
from tests.test_parse import _configure, _project, _upload


def _drain(times: int = 6) -> None:
    worker = Worker(
        get_engine(), hooks=[parse.on_job_finished, assets.on_job_finished, render.on_job_finished]
    )
    for _ in range(times):
        asyncio.run(worker.run_once())


@pytest.fixture
def project(client, sample_video, tmp_path, monkeypatch) -> str:
    monkeypatch.setattr(render, "_ENGINE", None)  # stitch explicitly, not in a background task
    _configure(client)
    project_id = _project(client)
    _upload(client, project_id, sample_video)
    client.post(f"/api/projects/{project_id}/parse")
    _drain()
    look = tmp_path / "sheep.png"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=white:s=64x64",
            "-frames:v",
            "1",
            str(look),
        ],
        check=True,
    )
    with look.open("rb") as handle:
        client.post(
            f"/api/projects/{project_id}/assets/char_1/images",
            files={"file": ("sheep.png", handle)},
        )
    return project_id


def _edit(client, project_id, change) -> None:
    url = f"/api/projects/{project_id}/storyboard"
    doc = client.get(url).json()
    change(doc)
    saved = client.put(url, json={"storyboard": doc, "base_version": doc["version"]})
    assert saved.status_code == 200, saved.text


def _stitch(project_id: str, render_id: str) -> None:
    render.stitch_render(get_engine(), render_id)


def test_render_with_every_treatment(client, project):
    def change(doc):
        shots = doc["shots"]
        # s001 stays a swap (new look for char_1).
        shots[1]["narrative"]["description"] = "Something else entirely"  # → regenerate
        shots[2]["edit"].update(
            {
                "treatment": "trim",
                "treatment_locked": True,
                "trim": {"keep_start": 2.0, "keep_end": 2.8},
            }
        )
        doc["shots"].append(
            {
                "shot_id": "s003a",
                "source": None,
                "narrative": {"description": "A new ending"},
                "edit": {"treatment": "new"},
                "generation": {"duration": 1.0},
            }
        )

    _edit(client, project, change)
    base = f"/api/projects/{project}/renders"
    started = client.post(base, json={"quality": "preview", "approve_frames": True})
    assert started.status_code == 202, started.text
    body = started.json()
    rid = body["id"]
    shots = body["shots"]
    assert {sid: s["treatment"] for sid, s in shots.items()} == {
        "s001": "swap",
        "s002": "regenerate",
        "s003": "trim",
        "s003a": "new",
    }
    assert shots["s003"]["status"] == "done" and shots["s003"]["duration"] == pytest.approx(0.8)
    assert shots["s001"]["status"] == "running" and shots["s001"]["mode"] == "edit"
    assert client.get(f"/api/projects/{project}").json()["status"] == "generating"
    assert client.post(base, json={}).status_code == 409  # one render at a time

    _drain()
    current = client.get(f"{base}/{rid}").json()
    assert current["status"] == "waiting"
    assert current["shots"]["s001"]["status"] == "done"
    assert current["shots"]["s002"]["status"] == "frame_ready"
    assert current["shots"]["s002"]["first_frame"].endswith(".png")
    assert (
        client.get(
            f"/api/projects/{project}/files/{current['shots']['s002']['first_frame']}"
        ).status_code
        == 200
    )

    # Redo one frame with a hint, then approve everything.
    redone = client.post(f"{base}/{rid}/shots/s002/redo-frame", json={"prompt": "brighter"})
    assert redone.json()["shots"]["s002"]["status"] == "running"
    assert client.post(f"{base}/{rid}/shots/s001/approve").status_code == 409
    _drain()
    approved = client.post(f"{base}/{rid}/approve-all").json()
    assert approved["shots"]["s002"]["step"] == "i2v"
    _drain()
    assert client.get(f"{base}/{rid}").json()["status"] == "stitching"

    _stitch(project, rid)
    done = client.get(f"{base}/{rid}").json()
    assert done["status"] == "done", done["error"]
    output = done["output"]
    info = probe(project_dir(project) / output)
    # 1 s swap + 1 s regenerate + 0.8 s trim + 1 s new shot
    assert info.duration == pytest.approx(3.8, abs=0.15)
    assert (info.width, info.height) == (160, 120)
    assert client.get(f"/api/projects/{project}").json()["status"] == "done"
    assert [r["id"] for r in client.get(base).json()] == [rid]


def test_failed_shot_can_be_retried_with_another_provider(client, project, monkeypatch):
    from app.capabilities.base import ContentRejected
    from app.providers.mock.adapter import MockAdapter

    original = MockAdapter.submit

    async def reject_video(self, capability, request):
        if capability == "video_edit_v2v" and not request.get("_ok"):
            raise ContentRejected("DataInspectionFailed", provider="mock")
        return await original(self, capability, request)

    monkeypatch.setattr(MockAdapter, "submit", reject_video)
    base = f"/api/projects/{project}/renders"
    rid = client.post(base, json={"approve_frames": False}).json()["id"]
    _drain()
    current = client.get(f"{base}/{rid}").json()
    assert current["status"] == "waiting"
    failed = current["shots"]["s001"]
    assert failed["status"] == "failed" and failed["error_type"] == "content_rejected"

    assert (
        client.post(f"{base}/{rid}/shots/s001/retry", json={"provider": "nobody"}).status_code
        == 422
    )
    assert client.post(f"{base}/{rid}/shots/nope/retry", json={}).status_code == 404
    monkeypatch.setattr(MockAdapter, "submit", original)
    retried = client.post(f"{base}/{rid}/shots/s001/retry", json={"provider": "mock"})
    assert retried.status_code == 200, retried.text
    assert retried.json()["shots"]["s001"]["provider"] == "mock"
    for shot_id in ("s002", "s003"):
        assert client.post(f"{base}/{rid}/shots/{shot_id}/retry", json={}).status_code == 200
    _drain()
    assert client.get(f"{base}/{rid}").json()["status"] == "stitching"

    cancelled = client.post(f"{base}/{rid}/cancel")
    assert cancelled.json()["status"] == "cancelled"
    assert client.post(f"{base}/{rid}/cancel").status_code == 409
    assert client.get(f"/api/projects/{project}").json()["status"] == "ready"


def test_render_preconditions(client, sample_video):
    project_id = _project(client)
    assert client.post(f"/api/projects/{project_id}/renders", json={}).status_code == 404
    assert client.post("/api/projects/missing/renders", json={}).status_code == 404
    assert client.get("/api/projects/missing/renders").status_code == 404
    assert client.get(f"/api/projects/{project_id}/renders/nope").status_code == 404


def test_swap_without_a_new_look_fails_the_shot(client, project):
    def change(doc):
        doc["shots"][1]["edit"].update({"treatment": "swap", "treatment_locked": True})

    _edit(client, project, change)
    url = f"/api/projects/{project}/assets/char_1/images/remove"
    doc = client.get(f"/api/projects/{project}/storyboard").json()
    client.post(url, json={"path": doc["characters"][0]["reference_images"][0]})
    body = client.post(f"/api/projects/{project}/renders", json={}).json()
    assert body["shots"]["s002"]["status"] == "failed"
    assert "new look" in body["shots"]["s002"]["error"]
    assert body["status"] == "waiting"


# -- planning ------------------------------------------------------------------------


def test_choose_video_model_prefers_edit_for_swaps():
    registry = get_registry()
    choice = render_plan.choose_video_model(registry, "dashscope", "wan2.2-animate-mix", "swap")
    assert (choice.model, choice.mode) == ("wan2.2-animate-mix", "swap")  # the user's pick
    best = render_plan.choose_video_model(registry, "dashscope", None, "swap")
    assert (best.model, best.mode, best.max_seconds) == ("wan2.7-videoedit", "edit", 10)
    motion = render_plan.choose_video_model(registry, "dashscope", "wan2.7-videoedit", "motion")
    assert motion.model == "wan2.2-animate-move"
    mock = render_plan.choose_video_model(registry, "mock", "mock-v2v", "motion")
    assert (mock.model, mock.mode) == ("mock-v2v", "motion")


def test_split_points():
    assert split_points(8.0, 10) == [(0.0, 8.0)]
    assert split_points(25.0, 10) == [(0.0, 8.333), (8.333, 16.667), (16.667, 25.0)]


def test_stitch_builds_audio_per_segment(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=red:s=160x120:r=25:d=3"]
        + [
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=3",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            str(source),
        ],
        check=True,
    )
    clip = normalize_clip(source, tmp_path / "a.mp4", width=160, height=120, fps=25, duration=1.0)
    out = stitch(
        [Segment(clip, 1.0, 0.5), Segment(clip, 1.0, None)],
        tmp_path / "out.mp4",
        audio_source=source,
        comment="AI",
    )
    info = probe(out)
    assert info.has_audio and info.duration == pytest.approx(2.0, abs=0.1)
    silent = stitch([Segment(clip, 1.0, 0.0)], tmp_path / "silent.mp4", audio_source=clip)
    assert not probe(silent).has_audio


def test_stale_and_foreign_jobs_are_ignored(engine):
    from app.models import Job, JobStatus

    with Session(engine) as session:
        asyncio.run(render.on_job_finished(session, Job(capability="tts", provider="mock")))
        job = Job(
            capability="image_edit",
            provider="mock",
            purpose=render.PURPOSE,
            status=JobStatus.SUCCEEDED,
            request={"render": {"id": "missing", "shot": "s001"}},
        )
        asyncio.run(render.on_job_finished(session, job))
        assert session.get(Render, "missing") is None
