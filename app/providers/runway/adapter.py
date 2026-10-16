"""Runway API: video edits (``video_edit_v2v``) and ``image_to_video``.

Written against https://docs.dev.runwayml.com/api.md on 2026-10-16 (not yet run against a
live key): POST /v1/video_to_video or /v1/image_to_video with ``X-Runway-Version``, then
poll GET /v1/tasks/{id}; ``output[0]`` is a URL valid for 24–48 h. Inputs go inline as
data URIs.
"""

import math
from typing import Any, ClassVar

from app.capabilities.base import (
    Artifact,
    Capability,
    ContentRejected,
    InputInvalid,
    JobHandle,
    PollResult,
    ProviderError,
    RemoteStatus,
)
from app.capabilities.generation import ImageToVideoRequest, VideoEditRequest, parse
from app.providers.http import InlineAdapter, data_url, send

API_VERSION = "2024-11-06"
STATUS = {
    "PENDING": RemoteStatus.PENDING,
    "THROTTLED": RemoteStatus.PENDING,
    "RUNNING": RemoteStatus.RUNNING,
    "SUCCEEDED": RemoteStatus.SUCCEEDED,
    "FAILED": RemoteStatus.FAILED,
    "CANCELLED": RemoteStatus.FAILED,
}
RATIO = "1280:720"
I2V_SECONDS = (2, 10)
PROMPT_LIMIT = 1000


class RunwayAdapter(InlineAdapter):
    provider_id: ClassVar[str] = "runway"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.VIDEO_EDIT_V2V, Capability.IMAGE_TO_VIDEO}
    )

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.credential('api_key')}",
            "X-Runway-Version": API_VERSION,
        }

    def _url(self, path: str) -> str:
        return f"{self.base_url()}{path}"

    async def test_connection(self) -> None:
        await send(
            self.http(),
            self.provider_id,
            "GET",
            self._url("/v1/organization"),
            headers=self._headers(),
        )

    async def submit(self, capability: Capability, request: dict[str, Any]) -> JobHandle:
        if capability == Capability.VIDEO_EDIT_V2V:
            path, body = (
                "/v1/video_to_video",
                self._edit(parse(VideoEditRequest, request, provider=self.provider_id)),
            )
        elif capability == Capability.IMAGE_TO_VIDEO:
            path, body = (
                "/v1/image_to_video",
                self._i2v(parse(ImageToVideoRequest, request, provider=self.provider_id)),
            )
        else:
            return await super().submit(capability, request)
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            self._url(path),
            headers=self._headers(),
            json=body,
        )
        task_id = response.json().get("id")
        if not task_id:
            raise ProviderError("Runway did not return a task id", provider=self.provider_id)
        return JobHandle(
            provider=self.provider_id, capability=capability, external_id=task_id, model=self.model
        )

    def _edit(self, request: VideoEditRequest) -> dict[str, Any]:
        if request.mode != "edit":
            raise InputInvalid(
                f"Runway does not do {request.mode}; use an edit", provider=self.provider_id
            )
        body: dict[str, Any] = {
            "model": self.require_model(),
            "videoUri": data_url(request.video),
            "promptText": (request.prompt or "")[:PROMPT_LIMIT],
        }
        option = self.model_option(Capability.VIDEO_EDIT_V2V)
        limit = option.references if option else 0
        if request.reference_images and limit:
            body["references"] = [{"uri": data_url(p)} for p in request.reference_images[:limit]]
        return body

    def _i2v(self, request: ImageToVideoRequest) -> dict[str, Any]:
        low, high = I2V_SECONDS
        return {
            "model": self.require_model(),
            "promptImage": data_url(request.first_frame),
            "promptText": request.prompt[:PROMPT_LIMIT],
            "ratio": RATIO,
            "duration": min(high, max(low, math.ceil(request.duration))),
        }

    async def _task(self, task_id: str) -> dict[str, Any]:
        response = await send(
            self.http(),
            self.provider_id,
            "GET",
            self._url(f"/v1/tasks/{task_id}"),
            headers=self._headers(),
        )
        return response.json()

    async def poll(self, handle: JobHandle) -> PollResult:
        body = await self._task(handle.external_id)
        status = STATUS.get(str(body.get("status")), RemoteStatus.FAILED)
        if status != RemoteStatus.FAILED:
            return PollResult(status=status, progress=body.get("progress"))
        code = str(body.get("failureCode") or "")
        rejected = "SAFETY" in code.upper() or "MODERATION" in code.upper()
        return PollResult(
            status=status,
            error_type=ContentRejected.error_type if rejected else "provider_error",
            error=f"{code}: {body.get('failure') or 'failed'}"
            if code
            else str(body.get("failure") or "failed"),
        )

    async def fetch_result(self, handle: JobHandle) -> Artifact:
        body = await self._task(handle.external_id)
        output = body.get("output") or []
        if not output:
            raise ProviderError("The finished task has no output", provider=self.provider_id)
        return Artifact(
            kind="video",
            uri=output[0],
            mime_type="video/mp4",
            meta={"model": self.model, "credits": (body.get("cost") or {}).get("credits")},
        )
