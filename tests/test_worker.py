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


async def test_hooks_run_when_a_job_finishes(engine):
    finished: list[tuple[str, JobStatus]] = []

    def hook(session, job):
        finished.append((job.id, job.status))

    def broken(session, job):
        raise RuntimeError("hook bug")

    with Session(engine) as session:
        ok = enqueue_job(session, capability=Capability.TTS, provider="mock", request={}).id
        bad = enqueue_job(session, capability=Capability.TTS, provider="nope", request={}).id
    worker = Worker(engine, hooks=[broken, hook])
    await worker.run_once()
    await worker.run_once()
    assert (bad, JobStatus.FAILED) in finished
    assert (ok, JobStatus.SUCCEEDED) in finished


async def test_new_credentials_build_a_new_adapter(engine, monkeypatch):
    from app.providers.credentials import save_credentials

    built: list[dict] = []
    import app.pipeline.worker as worker_module

    real = worker_module.build_adapter

    def spy(provider_id, **kwargs):
        built.append(kwargs["credentials"])
        return real("mock", model=kwargs.get("model"))

    monkeypatch.setattr(worker_module, "build_adapter", spy)
    worker = Worker(engine)
    with Session(engine) as session:
        save_credentials(session, "elevenlabs", {"api_key": "first-key-1111"})
        enqueue_job(session, capability=Capability.TTS, provider="elevenlabs", request={})
        enqueue_job(session, capability=Capability.TTS, provider="elevenlabs", request={})
    await worker.run_once()
    with Session(engine) as session:
        save_credentials(session, "elevenlabs", {"api_key": "second-key-2222"})
        enqueue_job(session, capability=Capability.TTS, provider="elevenlabs", request={})
    await worker.run_once()
    assert [c["api_key"] for c in built] == ["first-key-1111", "second-key-2222"]


async def test_rate_limited_submit_waits_and_retries(engine, monkeypatch):
    from app.capabilities.base import RateLimited
    from app.pipeline import worker as worker_module
    from app.providers.mock.adapter import MockAdapter

    monkeypatch.setattr(worker_module, "BACKOFF_S", (0.0, 0.0))
    original = MockAdapter.submit
    calls = {"n": 0}

    async def busy_once(self, capability, request):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimited("Throttling.RateQuota", provider="mock")
        return await original(self, capability, request)

    monkeypatch.setattr(MockAdapter, "submit", busy_once)
    with Session(engine) as session:
        job = enqueue_job(session, capability=Capability.LLM_TEXT, provider="mock", request={})
    worker = Worker(engine)
    await worker.run_once()
    assert _job(engine, job.id).status == JobStatus.QUEUED  # kept, not failed
    await worker.run_once()
    assert _job(engine, job.id).status == JobStatus.RUNNING


async def test_transient_poll_errors_keep_the_remote_task(engine, monkeypatch):
    from app.capabilities.base import InputInvalid, ProviderUnreachable
    from app.pipeline import worker as worker_module
    from app.providers.mock.adapter import MockAdapter

    monkeypatch.setattr(worker_module, "BACKOFF_S", (0.0, 0.0))
    monkeypatch.setattr(worker_module, "MAX_TRANSIENT_RETRIES", 2)
    with Session(engine) as session:
        job = enqueue_job(session, capability=Capability.LIPSYNC, provider="mock", request={})
    worker = Worker(engine)
    await worker.run_once()  # submitted

    async def unreachable(self, handle):
        raise ProviderUnreachable("Cannot reach the provider", provider="mock")

    monkeypatch.setattr(MockAdapter, "poll", unreachable)
    for _ in range(2):
        await worker.run_once()
        assert _job(engine, job.id).status == JobStatus.RUNNING
    await worker.run_once()  # retries used up
    failed = _job(engine, job.id)
    assert failed.status == JobStatus.FAILED and failed.error_type == "provider_unreachable"

    # A non-transient error on submit fails at once: it may have reached the provider.
    async def bad(self, capability, request):
        raise InputInvalid("bad", provider="mock")

    monkeypatch.setattr(MockAdapter, "submit", bad)
    with Session(engine) as session:
        other = enqueue_job(session, capability=Capability.TTS, provider="mock", request={})
    await worker.run_once()
    assert _job(engine, other.id).status == JobStatus.FAILED
