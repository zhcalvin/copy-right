"""Provider-neutral requests for the generation capabilities.

Inputs are local file paths; adapters upload or inline them as their API requires.
Results come back as :class:`~app.capabilities.base.Artifact` with ``uri`` set to the
provider's (usually short-lived) download URL. Callers download it promptly.
"""

from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, ValidationError

from app.capabilities.base import InputInvalid


class ImageEditRequest(BaseModel):
    """Edit or combine 1–3 reference images following ``prompt``."""

    images: list[str] = Field(min_length=1, max_length=3)
    prompt: str = Field(min_length=1, max_length=2000)
    # "width*height"; omitted = the provider's default (usually the last image's aspect).
    size: str | None = Field(default=None, pattern=r"^\d{3,4}\*\d{3,4}$")
    n: int = Field(default=1, ge=1, le=4)


class VideoEditRequest(BaseModel):
    """Re-render a source clip. ``swap`` replaces the main character with the one in
    ``reference_images`` while keeping the clip's motion, camera and lighting."""

    video: str
    reference_images: list[str] = Field(min_length=1)
    mode: Literal["swap"] = "swap"
    prompt: str | None = None
    quality: Literal["standard", "pro"] = "standard"


class ImageToVideoRequest(BaseModel):
    """Animate ``first_frame`` following ``prompt`` (treatments ``regenerate`` and ``new``)."""

    first_frame: str
    prompt: str = Field(min_length=1, max_length=1500)
    negative_prompt: str | None = Field(default=None, max_length=500)
    duration: float = Field(default=5.0, gt=0, le=15)
    resolution: Literal["720P", "1080P"] = "720P"


Request = TypeVar("Request", bound=BaseModel)


def parse(model: type[Request], raw: dict[str, Any], *, provider: str) -> Request:
    try:
        return model.model_validate(raw)
    except ValidationError as exc:
        first = exc.errors()[0]
        where = ".".join(str(p) for p in first["loc"]) or "request"
        raise InputInvalid(f"Bad request at {where}: {first['msg']}", provider=provider) from exc
