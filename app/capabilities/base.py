"""The 8 capability interfaces every provider adapter implements (docs/PLAN.md §3).

Business code talks only to :class:`Adapter`. It never imports a provider SDK.
"""

from abc import ABC, abstractmethod
from enum import StrEnum
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field


class Capability(StrEnum):
    VIDEO_UNDERSTANDING = "video_understanding"
    LLM_TEXT = "llm_text"
    IMAGE_EDIT = "image_edit"
    VIDEO_EDIT_V2V = "video_edit_v2v"
    IMAGE_TO_VIDEO = "image_to_video"
    TTS = "tts"
    LIPSYNC = "lipsync"
    ASR = "asr"


# Capabilities the user may switch off in the setup wizard.
OPTIONAL_CAPABILITIES = frozenset({Capability.TTS, Capability.LIPSYNC, Capability.ASR})


# ---------------------------------------------------------------------------
# Errors. ContentRejected is deliberately distinct: the UI offers a provider switch.
# ---------------------------------------------------------------------------


class ProviderError(Exception):
    """Base class for every adapter failure."""

    error_type: ClassVar[str] = "provider_error"

    def __init__(self, message: str, *, provider: str | None = None) -> None:
        super().__init__(message)
        self.provider = provider


class AuthError(ProviderError):
    error_type = "auth_error"


class QuotaError(ProviderError):
    error_type = "quota_error"


class ContentRejected(ProviderError):
    error_type = "content_rejected"


class InputInvalid(ProviderError):
    error_type = "input_invalid"


class ProviderTimeout(ProviderError):
    error_type = "provider_timeout"


# ---------------------------------------------------------------------------
# Adapter metadata
# ---------------------------------------------------------------------------

InputMode = Literal["url", "upload", "base64"]


class InputLimits(BaseModel):
    """What a provider accepts. The orchestrator pre-processes inputs to fit."""

    max_duration_s: float | None = None
    max_resolution: tuple[int, int] | None = None
    max_file_mb: float | None = None
    input_modes: tuple[InputMode, ...] = ("upload",)


class CredentialField(BaseModel):
    name: str
    label: str
    secret: bool = True
    required: bool = True
    env: str | None = None
    # Used when neither the environment nor the credential store has a value,
    # e.g. a provider's default base URL.
    default: str | None = None


# ---------------------------------------------------------------------------
# Job lifecycle
# ---------------------------------------------------------------------------


class RemoteStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class JobHandle(BaseModel):
    provider: str
    capability: Capability
    external_id: str
    model: str | None = None


class PollResult(BaseModel):
    status: RemoteStatus
    progress: float | None = None
    error_type: str | None = None
    error: str | None = None


ArtifactKind = Literal["json", "text", "image", "video", "audio"]


class Artifact(BaseModel):
    """A finished result. Media is a local path or URL; structured output goes in ``data``."""

    kind: ArtifactKind
    uri: str | None = None
    data: dict[str, Any] | None = None
    mime_type: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Adapter contract
# ---------------------------------------------------------------------------


class Adapter(ABC):
    """One provider's implementation of one or more capabilities.

    Synchronous providers do the work in :meth:`submit` and report ``SUCCEEDED`` on the
    first :meth:`poll`.
    """

    provider_id: ClassVar[str]
    capabilities: ClassVar[frozenset[Capability]]
    credential_fields: ClassVar[tuple[CredentialField, ...]] = ()

    def __init__(self, *, credentials: dict[str, str] | None = None, model: str | None = None):
        self.credentials = dict(credentials or {})
        self.model = model

    def supports(self, capability: Capability) -> bool:
        return capability in self.capabilities

    def input_limits(self, capability: Capability) -> InputLimits:
        return InputLimits()

    @abstractmethod
    async def submit(self, capability: Capability, request: dict[str, Any]) -> JobHandle: ...

    @abstractmethod
    async def poll(self, handle: JobHandle) -> PollResult: ...

    @abstractmethod
    async def fetch_result(self, handle: JobHandle) -> Artifact: ...

    @abstractmethod
    async def test_connection(self) -> None:
        """Cheaply verify credentials. Must never trigger billable generation.

        Raises a :class:`ProviderError` subclass on failure.
        """
