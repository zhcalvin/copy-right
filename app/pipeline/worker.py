"""In-process job worker.

Job state lives in SQLite. A job is marked ``running`` in the same commit that stores
its external task ID, so after a restart the worker resumes polling instead of
resubmitting (no double billing).
"""

import asyncio
import hashlib
import json
import logging
from collections.abc import Callable, Sequence

from sqlalchemy import Engine
from sqlmodel import Session, select

from app.capabilities.base import Adapter, Capability, JobHandle, ProviderError, RemoteStatus
from app.core.secrets import redact
from app.models import Job, JobStatus
from app.pipeline.jobs import update_job
from app.providers.credentials import load_stored, resolve_credentials
from app.providers.registry import ProviderNotImplemented, Registry, build_adapter, get_registry

logger = logging.getLogger(__name__)

# Called with (session, job) after a job reaches SUCCEEDED or FAILED.
JobHook = Callable[[Session, Job], None]


class Worker:
    def __init__(
        self,
        engine: Engine,
        *,
        poll_interval: float = 1.0,
        registry: Registry | None = None,
        hooks: Sequence[JobHook] = (),
    ) -> None:
        self._engine = engine
        self._poll_interval = poll_interval
        self._registry = registry or get_registry()
        self._hooks = tuple(hooks)
        self._adapters: dict[tuple[str, str | None, str], Adapter] = {}
        self._job_adapters: dict[str, Adapter] = {}
        self._task: asyncio.Task[None] | None = None

    # -- lifecycle -----------------------------------------------------------------

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run_forever(), name="copyright-worker")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_forever(self) -> None:
        while True:
            try:
                await self.run_once()
            except Exception:  # keep the worker alive; individual jobs record their errors
                logger.exception("worker iteration failed")
            await asyncio.sleep(self._poll_interval)

    # -- one tick ------------------------------------------------------------------

    async def run_once(self) -> None:
        with Session(self._engine) as session:
            active = session.exec(
                select(Job)
                .where(Job.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]))
                .order_by(Job.created_at)
            ).all()
            for job in active:
                await self._advance(session, job)

    async def _advance(self, session: Session, job: Job) -> None:
        try:
            adapter = self._adapter_for(session, job)
            if job.status == JobStatus.QUEUED:
                handle = await adapter.submit(Capability(job.capability), job.request)
                update_job(session, job, status=JobStatus.RUNNING, external_id=handle.external_id)
                return
            await self._poll(session, job, adapter)
        except ProviderError as exc:
            self._finish(
                session,
                job,
                status=JobStatus.FAILED,
                error_type=exc.error_type,
                error=redact(str(exc), self._secrets_for(job)),
            )
        except (ProviderNotImplemented, KeyError) as exc:
            self._finish(
                session, job, status=JobStatus.FAILED, error_type="not_implemented", error=str(exc)
            )

    def _finish(self, session: Session, job: Job, **changes: object) -> None:
        update_job(session, job, **changes)
        self._job_adapters = {k: v for k, v in self._job_adapters.items() if k != job.id}
        for hook in self._hooks:
            try:
                hook(session, job)
            except Exception:  # a broken hook must not wedge the queue
                logger.exception("job hook failed for job %s", job.id)

    async def _poll(self, session: Session, job: Job, adapter: Adapter) -> None:
        if job.external_id is None:
            self._finish(
                session,
                job,
                status=JobStatus.FAILED,
                error_type="provider_error",
                error="running job has no external id",
            )
            return
        handle = JobHandle(
            provider=job.provider,
            capability=Capability(job.capability),
            external_id=job.external_id,
            model=job.model,
        )
        result = await adapter.poll(handle)
        if result.status == RemoteStatus.SUCCEEDED:
            artifact = await adapter.fetch_result(handle)
            self._finish(session, job, status=JobStatus.SUCCEEDED, result=artifact.model_dump())
        elif result.status == RemoteStatus.FAILED:
            self._finish(
                session,
                job,
                status=JobStatus.FAILED,
                error_type=result.error_type or "provider_error",
                error=result.error,
            )

    def _secrets_for(self, job: Job) -> list[str]:
        adapter = self._job_adapters.get(job.id)
        return list(adapter.credentials.values()) if adapter else []

    def _adapter_for(self, session: Session, job: Job) -> Adapter:
        """One adapter per (provider, model, credentials).

        A job keeps the adapter that submitted it, because synchronous adapters hold the
        result in memory. New jobs pick up changed credentials.
        """
        if job.id in self._job_adapters:
            return self._job_adapters[job.id]
        spec = self._registry.provider(job.provider)
        stored = load_stored(session, job.provider) if spec.credentials else {}
        credentials = resolve_credentials(spec.credentials, stored)
        key = (job.provider, job.model, _fingerprint(credentials))
        adapter = self._adapters.get(key) or build_adapter(
            job.provider, credentials=credentials, model=job.model, registry=self._registry
        )
        self._adapters = {**self._adapters, key: adapter}
        self._job_adapters = {**self._job_adapters, job.id: adapter}
        return adapter


def _fingerprint(credentials: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(credentials, sort_keys=True).encode()).hexdigest()
