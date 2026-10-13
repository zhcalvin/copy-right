"""SQLite engine and session helpers."""

from collections.abc import Iterator
from functools import lru_cache
from typing import Annotated

from fastapi import Depends
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import get_settings


@lru_cache
def get_engine() -> Engine:
    settings = get_settings()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return create_engine(settings.database_url, connect_args={"check_same_thread": False})


def init_db() -> None:
    import app.models  # noqa: F401  (register tables on the metadata)

    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
