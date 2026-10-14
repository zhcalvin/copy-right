import asyncio

import pytest
from sqlmodel import Session

from app.capabilities.base import Capability
from app.capabilities.video_understanding import UnderstandingResult
from app.core.db import get_engine
from app.models import Job, JobStatus, Project, ProjectStatus
from app.pipeline import parse
from app.pipeline.jobs import enqueue_job, update_job
from app.pipeline.parse import PreparedShot, build_storyboard
from app.pipeline.worker import Worker
from app.providers.registry import get_registry
from tests.conftest import SAMPLE_DURATION


def _configure(client, video_provider: str = "mock") -> None:
    preset = get_registry().presets["demo"]
    selections = [
        {"capability": c, "provider": video_provider if c == "video_understanding" else p}
        for c, p in preset.capabilities.items()
    ]
    assert client.put("/api/settings/capabilities", json=selections).status_code == 200


def _project(client, name="Demo") -> str:
    return client.post("/api/projects", json={"name": name}).json()["id"]


def _upload(client, project_id, path, **form):
    with path.open("rb") as handle:
        return client.post(
            f"/api/projects/{project_id}/video",
            files={"file": (path.name, handle, "video/mp4")},
            data={"rights_confirmed": "true", **form},
        )


def _run_worker(times: int = 3) -> None:
    worker = Worker(get_engine(), hooks=[parse.on_job_finished])
    for _ in range(times):
        asyncio.run(worker.run_once())


def _prepared(count: int) -> list[PreparedShot]:
    return [
        PreparedShot(
            shot_id=f"s{i:03d}",
            start=i,
            end=i + 1,
            clip=f"clips/s{i:03d}.mp4",
            keyframes=[f"frames/s{i:03d}_0.jpg"],
            frames=["/abs/frame.jpg"],
        )
        for i in range(1, count + 1)
    ]


# -- end to end ------------------------------------------------------------------------


def test_upload_parse_and_read_storyboard(client, sample_video):
    _configure(client)
    project_id = _project(client)

    uploaded = _upload(client, project_id, sample_video)
    assert uploaded.status_code == 200, uploaded.text
    body = uploaded.json()
    assert body["has_video"] and body["source_file"] == "source/original.mp4"
    assert body["duration"] == pytest.approx(SAMPLE_DURATION, abs=0.05)

    started = client.post(f"/api/projects/{project_id}/parse", json={"threshold": 2.5})
    assert started.status_code == 202, started.text
    # TestClient runs the background preparation before returning.
    assert client.get(f"/api/projects/{project_id}").json()["stage"] == "describing"
    assert client.get(f"/api/projects/{project_id}/storyboard").status_code == 404

    _run_worker()
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["status"] == "ready" and project["stage"] is None

    storyboard = client.get(f"/api/projects/{project_id}/storyboard").json()
    assert storyboard["version"] == 1
    shots = storyboard["shots"]
    assert [s["shot_id"] for s in shots] == ["s001", "s002", "s003"]
    assert [round(s["source"]["start"], 1) for s in shots] == [0.0, 1.0, 2.0]
    assert shots[0]["narrative"]["description"].startswith("Mock description")
    assert shots[0]["assets"] == {
        "scene_id": "scene_1",
        "character_ids": ["char_1"],
        "prop_ids": [],
    }

    keyframe = client.get(f"/api/projects/{project_id}/files/{shots[1]['source']['keyframes'][0]}")
    assert keyframe.status_code == 200 and keyframe.headers["content-type"] == "image/jpeg"
    clip = client.get(f"/api/projects/{project_id}/files/{shots[1]['source']['clip']}")
    assert clip.status_code == 200

    # Parsing again replaces clips and frames and saves the next version.
    client.post(f"/api/projects/{project_id}/parse")
    _run_worker()
    assert client.get(f"/api/projects/{project_id}/storyboard").json()["version"] == 2


def test_files_cannot_escape_the_project(client, sample_video):
    project_id = _project(client)
    _upload(client, project_id, sample_video)
    for path in ("../../copyright.db", "source/missing.mp4", "%2e%2e/%2e%2e/copyright.db"):
        assert client.get(f"/api/projects/{project_id}/files/{path}").status_code == 404
    assert client.get("/api/projects/nope/files/source/original.mp4").status_code == 404


def test_upload_validation(client, sample_video, tmp_path, monkeypatch):
    project_id = _project(client)
    no_rights = _upload(client, project_id, sample_video, rights_confirmed="false")
    assert no_rights.status_code == 422 and "own this footage" in no_rights.json()["detail"]

    text_file = tmp_path / "notes.txt"
    text_file.write_text("hello")
    assert _upload(client, project_id, text_file).status_code == 422

    fake = tmp_path / "fake.mp4"
    fake.write_text("not a video")
    bad = _upload(client, project_id, fake)
    assert bad.status_code == 422 and "Not a readable video" in bad.json()["detail"]

    from app.api import projects

    monkeypatch.setattr(projects, "MAX_DURATION_S", 1.0)
    too_long = _upload(client, project_id, sample_video)
    assert too_long.status_code == 422 and "limit" in too_long.json()["detail"]

    monkeypatch.setattr(projects, "MAX_UPLOAD_BYTES", 10)
    assert _upload(client, project_id, sample_video).status_code == 413
    assert not list((tmp_path / "data").rglob("*.part"))

    assert _upload(client, "missing", sample_video).status_code == 404


def test_parse_preconditions(client, sample_video):
    project_id = _project(client)
    assert client.post(f"/api/projects/{project_id}/parse").status_code == 422  # no video
    _upload(client, project_id, sample_video)
    assert client.post(f"/api/projects/{project_id}/parse").status_code == 409  # no settings
    bad = client.post(f"/api/projects/{project_id}/parse", json={"threshold": 99})
    assert bad.status_code == 422

    _configure(client)
    assert client.post(f"/api/projects/{project_id}/parse").status_code == 202
    assert client.post(f"/api/projects/{project_id}/parse").status_code == 409  # already parsing
    upload = _upload(client, project_id, sample_video)
    assert upload.status_code == 409
    assert client.post("/api/projects/missing/parse").status_code == 404


def test_provider_failure_marks_the_project_failed(client, sample_video):
    _configure(client, video_provider="dashscope")  # no key configured
    project_id = _project(client)
    _upload(client, project_id, sample_video)
    client.post(f"/api/projects/{project_id}/parse")
    _run_worker(1)
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["status"] == "failed"
    assert project["error_type"] == "auth_error" and "api_key" in project["error"]


def test_media_failure_marks_the_project_failed(client, sample_video):
    _configure(client)
    project_id = _project(client)
    _upload(client, project_id, sample_video)
    with Session(get_engine()) as session:
        project = session.get(Project, project_id)
        project.source_video_path = str(sample_video.with_name("vanished.mp4"))
        session.add(project)
        session.commit()
    client.post(f"/api/projects/{project_id}/parse")
    project = client.get(f"/api/projects/{project_id}").json()
    assert project["status"] == "failed" and project["error_type"] == "media_error"


# -- storyboard assembly ---------------------------------------------------------------


def test_build_storyboard_keeps_detected_timing_and_fixes_references():
    result = UnderstandingResult.model_validate(
        {
            "characters": [{"character_id": "char_1", "name": ""}],
            "scenes": [{"scene_id": "scene_1"}],
            "shots": [
                {
                    "shot_id": "s001",
                    "description": "Hello",
                    "characters": [{"character_id": "char_1"}, {"character_id": "char_9"}],
                    "dialogue": [
                        {"character_id": "char_404", "text": "Hi"},
                        {"text": "  "},
                    ],
                    "scene_id": "scene_404",
                },
                {"shot_id": "s999", "description": "Not a detected shot"},
            ],
        }
    )
    doc = build_storyboard("p1", 4, _prepared(2), result)
    first, second = doc.shots
    assert doc.version == 4
    assert {c.character_id: c.name for c in doc.characters} == {
        "char_1": "char_1",
        "char_9": "char_9",
    }
    assert first.assets.character_ids == ["char_1", "char_9"]
    assert first.assets.scene_id is None
    assert [(d.character_id, d.text) for d in first.narrative.dialogue] == [(None, "Hi")]
    assert (first.source.start, first.source.end) == (1, 2)
    assert second.narrative.description == ""  # the model skipped it


def test_recover_interrupted(engine):
    with Session(engine) as session:
        stuck = Project(name="stuck", status=ProjectStatus.PARSING)
        busy = Project(name="busy", status=ProjectStatus.PARSING)
        session.add_all([stuck, busy])
        session.commit()
        enqueue_job(
            session,
            capability=Capability.VIDEO_UNDERSTANDING,
            provider="mock",
            request={},
            project_id=busy.id,
            purpose=parse.PURPOSE,
        )
        assert parse.recover_interrupted(session) == 1
        session.refresh(stuck)
        session.refresh(busy)
        assert stuck.status == ProjectStatus.FAILED and stuck.error_type == "interrupted"
        assert busy.status == ProjectStatus.PARSING


def test_hook_ignores_other_jobs_and_handles_bad_results(engine):
    with Session(engine) as session:
        project = Project(name="p", status=ProjectStatus.PARSING)
        session.add(project)
        session.commit()
        other = enqueue_job(session, capability=Capability.TTS, provider="mock", request={})
        parse.on_job_finished(session, other)  # not a parse job: no effect

        job = enqueue_job(
            session,
            capability=Capability.VIDEO_UNDERSTANDING,
            provider="mock",
            request={"shots": [{"shot_id": "s1"}]},  # not a PreparedShot
            project_id=project.id,
            purpose=parse.PURPOSE,
        )
        update_job(session, job, status=JobStatus.SUCCEEDED, result={"data": {}})
        parse.on_job_finished(session, job)
        session.refresh(project)
        assert project.status == ProjectStatus.FAILED and project.error_type == "parse_error"

        orphan = Job(capability="video_understanding", provider="mock", purpose=parse.PURPOSE)
        parse.on_job_finished(session, orphan)  # no project: no effect
