"""Shared helpers for HTTP-based adapters."""

import base64
import mimetypes
import uuid
from pathlib import Path
from typing import Any, ClassVar

import httpx

from app.capabilities.base import (
    Adapter,
    Artifact,
    AuthError,
    Capability,
    ContentRejected,
    InputInvalid,
    JobHandle,
    PollResult,
    ProviderError,
    ProviderTimeout,
    QuotaError,
    RemoteStatus,
)

# Substrings (lower case, underscores and spaces removed) that providers use in error codes
# or messages. DashScope reports moderation as "DataInspectionFailed" or
# "data_inspection_failed"; Gemini answers a bad key with HTTP 400 "API_KEY_INVALID".
MODERATION_MARKERS = ("datainspectionfailed", "inappropriate", "safety", "moderation")
AUTH_MARKERS = ("apikeyinvalid", "invalidapikey")


def _flat(text: str) -> str:
    return text.lower().replace("_", "").replace(" ", "")


def data_url(path: str | Path) -> str:
    file = Path(path)
    mime = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    return f"data:{mime};base64,{base64.b64encode(file.read_bytes()).decode('ascii')}"


def _error_text(response: httpx.Response) -> str:
    try:
        body: Any = response.json()
    except ValueError:
        return response.text[:300]
    error = body.get("error", body) if isinstance(body, dict) else body
    if isinstance(error, dict):
        parts = [str(error.get(k)) for k in ("code", "message", "status") if error.get(k)]
        return ": ".join(parts)[:300]
    return str(error)[:300]


def raise_for_response(response: httpx.Response, provider: str) -> None:
    """Map an HTTP error onto the shared error types (docs/PLAN.md §3)."""
    code = response.status_code
    if code < 400:
        return
    detail = _error_text(response)
    message = f"HTTP {code}: {detail}" if detail else f"HTTP {code}"
    flat = _flat(detail)
    if any(marker in flat for marker in MODERATION_MARKERS):
        raise ContentRejected(message, provider=provider)
    if code in (401, 403) or any(marker in flat for marker in AUTH_MARKERS):
        raise AuthError(message, provider=provider)
    if code in (402, 429):
        raise QuotaError(message, provider=provider)
    if code in (408, 504):
        raise ProviderTimeout(message, provider=provider)
    if 400 <= code < 500:
        raise InputInvalid(message, provider=provider)
    raise ProviderError(message, provider=provider)


async def send(
    client: httpx.AsyncClient,
    provider: str,
    method: str,
    url: str,
    *,
    retries: int = 1,
    **kwargs: Any,
) -> httpx.Response:
    """Send a request, retrying dropped connections (not timeouts or HTTP errors)."""
    for attempt in range(retries + 1):
        try:
            response = await client.request(method, url, **kwargs)
            break
        except httpx.TimeoutException as exc:
            raise ProviderTimeout("The provider did not answer in time", provider=provider) from exc
        except httpx.TransportError as exc:
            if attempt < retries:
                continue
            raise ProviderError(
                f"Cannot reach the provider: {type(exc).__name__}", provider=provider
            ) from exc
    raise_for_response(response, provider)
    return response


class InlineAdapter(Adapter):
    """Base for synchronous APIs: the work happens in :meth:`submit`.

    Results are kept in memory until fetched. After a restart a running job's result is
    gone, so :meth:`poll` reports it as failed and the caller can retry.
    """

    timeout_s: ClassVar[float] = 300.0

    def __init__(self, *, client: httpx.AsyncClient | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._client = client
        self._results: dict[str, Artifact] = {}

    def http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_s)
        return self._client

    async def run(self, capability: Capability, request: dict[str, Any]) -> Artifact:
        raise InputInvalid(
            f"{self.provider_id} does not implement {capability} yet", provider=self.provider_id
        )

    async def submit(self, capability: Capability, request: dict[str, Any]) -> JobHandle:
        if not self.supports(capability):
            raise InputInvalid(
                f"{self.provider_id} does not implement {capability} yet",
                provider=self.provider_id,
            )
        artifact = await self.run(capability, request)
        external_id = f"{self.provider_id}-{uuid.uuid4().hex}"
        self._results = {**self._results, external_id: artifact}
        return JobHandle(
            provider=self.provider_id,
            capability=capability,
            external_id=external_id,
            model=self.model,
        )

    async def poll(self, handle: JobHandle) -> PollResult:
        if handle.external_id in self._results:
            return PollResult(status=RemoteStatus.SUCCEEDED, progress=1.0)
        return PollResult(
            status=RemoteStatus.FAILED,
            error_type="provider_error",
            error="The result was lost when the app restarted; please run it again",
        )

    async def fetch_result(self, handle: JobHandle) -> Artifact:
        results = dict(self._results)
        artifact = results.pop(handle.external_id, None)
        if artifact is None:
            raise ProviderError("Result not found", provider=self.provider_id)
        self._results = results
        return artifact

    def credential(self, name: str) -> str:
        value = self.credentials.get(name)
        if not value:
            raise AuthError(f"Missing credentials: {name}", provider=self.provider_id)
        return value

    def base_url(self) -> str:
        # The default lives in registry.yaml and arrives through credential resolution.
        return self.credential("base_url").rstrip("/")

    def require_model(self) -> str:
        if not self.model:
            raise InputInvalid("No model selected", provider=self.provider_id)
        return self.model
