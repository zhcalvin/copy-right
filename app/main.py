"""FastAPI entry point: ``uv run uvicorn app.main:app --reload --port 8000``."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.router import api_router
from app.core.config import get_settings
from app.core.db import get_engine, init_db
from app.pipeline.worker import Worker


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    init_db()
    worker = Worker(get_engine(), poll_interval=settings.worker_poll_interval)
    if settings.worker_enabled:
        worker.start()
    app.state.worker = worker
    try:
        yield
    finally:
        await worker.stop()


app = FastAPI(title="Copy-Right", version="0.1.0", lifespan=lifespan)
app.include_router(api_router)
