from sqlmodel import Session

from app.capabilities.base import Capability
from app.models import Job, JobStatus
from app.pipeline.jobs import enqueue_job
from app.pipeline.worker import Worker


def _job(engine, job_id: str) -> Job:
    with Session(engine) as session:
        return session.get(Job, job_id)


async def test_sync_job_runs_to_success(engine):
    with Session(engine) as session:
        job = enqueue_job(
            session, capability=Capability.VIDEO_UNDERSTANDING, provider="mock", request={}
        )
    worker = Worker(engine)

    await worker.run_once()  # submit
    assert _job(engine, job.id).status == JobStatus.RUNNING
    assert _job(engine, job.id).external_id

    await worker.run_once()  # poll -> succeeded
    done = _job(engine, job.id)
    assert done.status == JobStatus.SUCCEEDED
    assert done.result["kind"] == "json"
    assert len(done.result["data"]["shots"]) == 3


async def test_async_job_polls_until_done(engine):
    with Session(engine) as session:
        job = enqueue_job(
            session, capability=Capability.IMAGE_TO_VIDEO, provider="mock", request={}
        )
    worker = Worker(engine)

    await worker.run_once()  # submit
    await worker.run_once()  # first poll: still running
    assert _job(engine, job.id).status == JobStatus.RUNNING
    await worker.run_once()
    assert _job(engine, job.id).status == JobStatus.SUCCEEDED


async def test_restart_resumes_polling_without_resubmitting(engine, monkeypatch):
    from app.providers.mock.adapter import MockAdapter

    with Session(engine) as session:
        job = enqueue_job(session, capability=Capability.LIPSYNC, provider="mock", request={})
    await Worker(engine).run_once()
    external_id = _job(engine, job.id).external_id

    async def fail_if_resubmitted(self, capability, request):
        raise AssertionError("job was resubmitted after restart")

    monkeypatch.setattr(MockAdapter, "submit", fail_if_resubmitted)
    restarted = Worker(engine)  # fresh process: no in-memory state
    await restarted.run_once()
    await restarted.run_once()
    done = _job(engine, job.id)
    assert done.status == JobStatus.SUCCEEDED
    assert done.external_id == external_id


async def test_unimplemented_provider_fails_job(engine):
    with Session(engine) as session:
        job = enqueue_job(session, capability=Capability.TTS, provider="elevenlabs", request={})
    await Worker(engine).run_once()
    failed = _job(engine, job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error_type == "not_implemented"


async def test_unknown_provider_fails_job(engine):
    with Session(engine) as session:
        job = enqueue_job(session, capability=Capability.TTS, provider="nope", request={})
    await Worker(engine).run_once()
    assert _job(engine, job.id).status == JobStatus.FAILED


async def test_content_rejected_is_recorded_distinctly(engine, monkeypatch):
    from app.capabilities.base import ContentRejected
    from app.providers.mock.adapter import MockAdapter

    async def reject(self, capability, request):
        raise ContentRejected("face swap refused", provider="mock")

    monkeypatch.setattr(MockAdapter, "submit", reject)
    with Session(engine) as session:
        job = enqueue_job(
            session, capability=Capability.VIDEO_EDIT_V2V, provider="mock", request={}
        )
    await Worker(engine).run_once()
    failed = _job(engine, job.id)
    assert failed.status == JobStatus.FAILED
    assert failed.error_type == "content_rejected"
