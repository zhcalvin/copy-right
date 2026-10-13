"""Create and query jobs. The worker picks them up from the database."""

from typing import Any

from sqlmodel import Session

from app.capabilities.base import Capability
from app.models import Job, utcnow


def enqueue_job(
    session: Session,
    *,
    capability: Capability,
    provider: str,
    request: dict[str, Any],
    model: str | None = None,
    project_id: str | None = None,
    shot_id: str | None = None,
) -> Job:
    job = Job(
        capability=capability,
        provider=provider,
        model=model,
        request=request,
        project_id=project_id,
        shot_id=shot_id,
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def update_job(session: Session, job: Job, **changes: Any) -> Job:
    """Apply changes to a persisted job and commit them."""
    job.sqlmodel_update({**changes, "updated_at": utcnow()})
    session.add(job)
    session.commit()
    session.refresh(job)
    return job
