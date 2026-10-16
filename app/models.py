"""SQLite tables (see docs/PLAN.md §6)."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return uuid4().hex


class ProjectStatus(StrEnum):
    CREATED = "created"
    PARSING = "parsing"
    READY = "ready"
    GENERATING = "generating"
    DONE = "done"
    FAILED = "failed"


class StoryboardSource(StrEnum):
    AUTO_PARSE = "auto_parse"
    USER_EDIT = "user_edit"
    LLM_EDIT = "llm_edit"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_JOB_STATUSES = frozenset({JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED})


class Project(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    name: str
    source_video_path: str | None = None
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    status: ProjectStatus = ProjectStatus.CREATED
    # What a running pipeline step is doing, e.g. "detecting" or "describing".
    stage: str | None = None
    error_type: str | None = None
    error: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Storyboard(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str = Field(foreign_key="project.id", index=True)
    version: int
    content: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    source: StoryboardSource
    # A short human-readable summary, e.g. the plot instruction or "Restored version 2".
    note: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Job(SQLModel, table=True):
    id: str = Field(default_factory=new_id, primary_key=True)
    project_id: str | None = Field(default=None, foreign_key="project.id", index=True)
    shot_id: str | None = None
    # Which pipeline step owns the job, so its result can be applied when it finishes.
    purpose: str | None = Field(default=None, index=True)
    capability: str
    provider: str
    model: str | None = None
    request: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    external_id: str | None = None
    status: JobStatus = Field(default=JobStatus.QUEUED, index=True)
    result: dict[str, Any] | None = Field(default=None, sa_column=Column(JSON))
    error_type: str | None = None
    error: str | None = None
    cost_estimate: float | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class CapabilitySetting(SQLModel, table=True):
    capability: str = Field(primary_key=True)
    provider: str | None = None
    model: str | None = None
    enabled: bool = True
    updated_at: datetime = Field(default_factory=utcnow)


class Credential(SQLModel, table=True):
    provider: str = Field(primary_key=True)
    encrypted: bytes
    last4: str
    updated_at: datetime = Field(default_factory=utcnow)


class AppSetting(SQLModel, table=True):
    """Small key/value settings, e.g. the chosen storage backend."""

    key: str = Field(primary_key=True)
    value: dict[str, Any] = Field(sa_column=Column(JSON, nullable=False))
    updated_at: datetime = Field(default_factory=utcnow)
