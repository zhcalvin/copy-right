from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.db import SessionDep
from app.models import Project, ProjectStatus
from app.storage.local import ensure_project_dirs

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class ProjectRead(BaseModel):
    id: str
    name: str
    status: ProjectStatus
    duration: float | None
    created_at: datetime


@router.get("")
def list_projects(session: SessionDep) -> list[ProjectRead]:
    projects = session.exec(select(Project).order_by(Project.created_at.desc())).all()
    return [ProjectRead.model_validate(p, from_attributes=True) for p in projects]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, session: SessionDep) -> ProjectRead:
    project = Project(name=body.name.strip())
    if not project.name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Project name cannot be empty")
    session.add(project)
    session.commit()
    session.refresh(project)
    ensure_project_dirs(project.id)
    return ProjectRead.model_validate(project, from_attributes=True)


@router.get("/{project_id}")
def get_project(project_id: str, session: SessionDep) -> ProjectRead:
    project = session.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Project not found")
    return ProjectRead.model_validate(project, from_attributes=True)
