"""Kling image-to-video.

Written from Kling's public API reference (task API: POST /v1/videos/image2video, poll
GET /v1/videos/image2video/{task_id}, result in ``data.task_result.videos[0].url``); not
yet run against a live key. Kling lip-sync needs a public video URL, so it waits for
object storage support.
"""

import base64
from pathlib import Path
from typing import Any, ClassVar

from app.capabilities.base import (
    Artifact,
    Capability,
    ContentRejected,
    JobHandle,
    PollResult,
    ProviderError,
    RemoteStatus,
)
from app.capabilities.generation import ImageToVideoRequest, parse
from app.providers.http import InlineAdapter, send

STATUS = {
    "submitted": RemoteStatus.PENDING,
    "processing": RemoteStatus.RUNNING,
    "succeed": RemoteStatus.SUCCEEDED,
    "failed": RemoteStatus.FAILED,
}
MODES = {"720P": "std", "1080P": "pro"}
PATH = "/v1/videos/image2video"


class KlingAdapter(InlineAdapter):
    provider_id: ClassVar[str] = "kling"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.IMAGE_TO_VIDEO})

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.credential('api_key')}"}

    async def test_connection(self) -> None:
        await send(
            self.http(),
            self.provider_id,
            "GET",
            f"{self.base_url()}/account/costs",
            headers=self._headers(),
        )

    async def submit(self, capability: Capability, request: dict[str, Any]) -> JobHandle:
        if capability != Capability.IMAGE_TO_VIDEO:
            return await super().submit(capability, request)
        parsed = parse(ImageToVideoRequest, request, provider=self.provider_id)
        body: dict[str, Any] = {
            "model_name": self.require_model(),
            "image": base64.b64encode(Path(parsed.first_frame).read_bytes()).decode("ascii"),
            "prompt": parsed.prompt[:2500],
            "mode": MODES.get(parsed.resolution, "std"),
            "duration": "5" if parsed.duration <= 5 else "10",
        }
        if parsed.negative_prompt:
            body["negative_prompt"] = parsed.negative_prompt
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            f"{self.base_url()}{PATH}",
            headers=self._headers(),
            json=body,
        )
        data = self._data(response.json())
        task_id = data.get("task_id")
        if not task_id:
            raise ProviderError("Kling did not return a task id", provider=self.provider_id)
        return JobHandle(
            provider=self.provider_id, capability=capability, external_id=task_id, model=self.model
        )

    def _data(self, body: dict[str, Any]) -> dict[str, Any]:
        code = body.get("code", 0)
        if code not in (0, None):
            message = f"{code}: {body.get('message', 'error')}"
            if "risk" in message.lower() or "sensitive" in message.lower():
                raise ContentRejected(message, provider=self.provider_id)
            raise ProviderError(message, provider=self.provider_id)
        return body.get("data") or {}

    async def _task(self, task_id: str) -> dict[str, Any]:
        response = await send(
            self.http(),
            self.provider_id,
            "GET",
            f"{self.base_url()}{PATH}/{task_id}",
            headers=self._headers(),
        )
        return self._data(response.json())

    async def poll(self, handle: JobHandle) -> PollResult:
        data = await self._task(handle.external_id)
        status = STATUS.get(str(data.get("task_status")), RemoteStatus.FAILED)
        if status != RemoteStatus.FAILED:
            return PollResult(status=status)
        message = str(data.get("task_status_msg") or "failed")
        rejected = "risk" in message.lower() or "sensitive" in message.lower()
        return PollResult(
            status=status,
            error_type=ContentRejected.error_type if rejected else "provider_error",
            error=message,
        )

    async def fetch_result(self, handle: JobHandle) -> Artifact:
        data = await self._task(handle.external_id)
        videos = (data.get("task_result") or {}).get("videos") or []
        if not videos or not videos[0].get("url"):
            raise ProviderError("The finished task has no video", provider=self.provider_id)
        return Artifact(
            kind="video", uri=videos[0]["url"], mime_type="video/mp4", meta={"model": self.model}
        )
