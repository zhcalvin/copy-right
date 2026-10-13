from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.core.db import SessionDep
from app.models import Job

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(session: SessionDep, project_id: str | None = None) -> list[Job]:
    query = select(Job).order_by(Job.created_at.desc())
    if project_id is not None:
        query = query.where(Job.project_id == project_id)
    return list(session.exec(query).all())


@router.get("/{job_id}")
def get_job(job_id: str, session: SessionDep) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Job not found")
    return job
