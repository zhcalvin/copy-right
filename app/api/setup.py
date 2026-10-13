from fastapi import APIRouter
from pydantic import BaseModel

from app.core.db import SessionDep
from app.providers.settings import is_configured

router = APIRouter(prefix="/setup", tags=["setup"])


class SetupStatus(BaseModel):
    configured: bool


@router.get("/status")
def setup_status(session: SessionDep) -> SetupStatus:
    """The frontend sends the user to the /setup wizard while this is false."""
    return SetupStatus(configured=is_configured(session))
