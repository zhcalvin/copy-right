"""Provider-neutral requests for ``tts`` and ``lipsync`` (docs/PLAN.md §7 step 6).

Inputs are local paths; results come back as an Artifact with a short-lived ``uri``.
"""

from typing import Any

from pydantic import BaseModel, Field

from app.capabilities.generation import parse

MAX_TTS_CHARS = 600  # the strictest default provider limit (Qwen TTS)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_TTS_CHARS)
    # A voice id from the provider's registry ``voices``; None = the provider's first.
    voice: str | None = None
    language: str | None = None  # "en", "zh-CN"; None = let the provider detect it
    speed: float = Field(default=1.0, ge=0.5, le=2.0)


class LipsyncRequest(BaseModel):
    """Re-time the mouth of the main speaker in ``video`` to ``audio``."""

    video: str
    audio: str
    # A clear photo of the speaker's face, for shots with several faces.
    face_image: str | None = None


def parse_speech(raw: dict[str, Any], *, provider: str) -> SpeechRequest:
    return parse(SpeechRequest, raw, provider=provider)


def parse_lipsync(raw: dict[str, Any], *, provider: str) -> LipsyncRequest:
    return parse(LipsyncRequest, raw, provider=provider)
