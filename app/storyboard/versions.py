"""Storyboard version history. Every change is saved as a new version (docs/PLAN.md §6)."""

from datetime import datetime

from pydantic import BaseModel, ValidationError
from sqlmodel import Session, func, select

from app.models import Storyboard, StoryboardSource
from app.storyboard.diff import affected_shots, refresh_edits
from app.storyboard.schema import StoryboardDoc


class VersionError(Exception):
    def __init__(self, message: str, *, error_type: str = "input_invalid") -> None:
        super().__init__(message)
        self.error_type = error_type


class VersionInfo(BaseModel):
    version: int
    source: StoryboardSource
    note: str | None
    created_at: datetime
    shots: int


def _query(project_id: str):
    return select(Storyboard).where(Storyboard.project_id == project_id)


def latest_row(session: Session, project_id: str) -> Storyboard | None:
    return session.exec(_query(project_id).order_by(Storyboard.version.desc())).first()


def row_for(session: Session, project_id: str, version: int) -> Storyboard | None:
    return session.exec(_query(project_id).where(Storyboard.version == version)).first()


def baseline_row(session: Session, project_id: str) -> Storyboard | None:
    """The latest auto-parsed version: the one that matches the clips on disk."""
    return session.exec(
        _query(project_id)
        .where(Storyboard.source == StoryboardSource.AUTO_PARSE)
        .order_by(Storyboard.version.desc())
    ).first()


def load(row: Storyboard) -> StoryboardDoc:
    return StoryboardDoc.model_validate(row.content)


def latest_doc(session: Session, project_id: str) -> StoryboardDoc:
    row = latest_row(session, project_id)
    if row is None:
        raise VersionError("This project has no storyboard yet", error_type="not_found")
    return load(row)


def baseline_doc(session: Session, project_id: str) -> StoryboardDoc:
    row = baseline_row(session, project_id)
    if row is None:
        raise VersionError("This project has no parsed storyboard yet", error_type="not_found")
    return load(row)


def next_version(session: Session, project_id: str) -> int:
    latest = session.exec(
        select(func.max(Storyboard.version)).where(Storyboard.project_id == project_id)
    ).one()
    return (latest or 0) + 1


def list_versions(session: Session, project_id: str) -> list[VersionInfo]:
    rows = session.exec(_query(project_id).order_by(Storyboard.version.desc())).all()
    return [
        VersionInfo(
            version=row.version,
            source=row.source,
            note=row.note,
            created_at=row.created_at,
            shots=len(row.content.get("shots", [])),
        )
        for row in rows
    ]


def save_version(
    session: Session,
    project_id: str,
    doc: StoryboardDoc | dict,
    *,
    source: StoryboardSource,
    note: str | None = None,
    base_version: int | None = None,
) -> tuple[StoryboardDoc, list[str]]:
    """Validate an edited storyboard, refresh its treatments and save it as version N+1.

    ``base_version`` is the version the editor started from; if another change landed in
    the meantime the save is refused instead of silently overwriting it.
    Returns the saved document and the IDs of the shots that changed.
    """
    current = latest_row(session, project_id)
    if current is None:
        raise VersionError("This project has no storyboard yet", error_type="not_found")
    if base_version is not None and base_version != current.version:
        raise VersionError(
            f"The storyboard changed since version {base_version}; reload and try again",
            error_type="conflict",
        )
    version = next_version(session, project_id)
    raw = doc.model_dump(mode="json") if isinstance(doc, StoryboardDoc) else dict(doc)
    try:
        parsed = StoryboardDoc.model_validate({**raw, "project_id": project_id, "version": version})
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "storyboard"
        raise VersionError(f"Invalid storyboard at {where}: {first['msg']}") from exc
    refreshed = refresh_edits(baseline_doc(session, project_id), parsed)
    changed = affected_shots(load(current), refreshed)
    session.add(
        Storyboard(
            project_id=project_id,
            version=version,
            content=refreshed.model_dump(mode="json"),
            source=source,
            note=note,
        )
    )
    session.commit()
    return refreshed, changed


def restore_version(session: Session, project_id: str, version: int) -> StoryboardDoc:
    """Save an earlier version's content as the newest version."""
    row = row_for(session, project_id, version)
    if row is None:
        raise VersionError(f"Version {version} does not exist", error_type="not_found")
    doc, _ = save_version(
        session,
        project_id,
        row.content,
        source=StoryboardSource.USER_EDIT,
        note=f"Restored version {version}",
    )
    return doc
