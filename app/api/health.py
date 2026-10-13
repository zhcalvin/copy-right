from fastapi import APIRouter
from pydantic import BaseModel

from app.tools.media import ffmpeg_available

router = APIRouter(tags=["health"])


class Health(BaseModel):
    status: str
    ffmpeg: bool


@router.get("/health")
def health() -> Health:
    return Health(status="ok", ffmpeg=ffmpeg_available())
