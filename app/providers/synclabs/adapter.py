"""Sync Labs (sync.so) lip-sync.

Checked against the OpenAPI schema at https://sync.so/docs/openapi.json on 2026-10-16
(not yet run against a live key): POST /v2/generate as multipart with the video and audio
files, then GET /v2/generate/{id} until COMPLETED; ``outputUrl`` is the result.
"""

import asyncio
import json
from pathlib import Path
from typing import Any, ClassVar

from app.capabilities.audio import parse_lipsync
from app.capabilities.base import (
    Artifact,
    Capability,
    ContentRejected,
    JobHandle,
    PollResult,
    ProviderError,
    RemoteStatus,
)
from app.providers.http import InlineAdapter, send

STATUS = {
    "PENDING": RemoteStatus.PENDING,
    "PROCESSING": RemoteStatus.RUNNING,
    "COMPLETED": RemoteStatus.SUCCEEDED,
    "FAILED": RemoteStatus.FAILED,
    "REJECTED": RemoteStatus.FAILED,
}


class SyncLabsAdapter(InlineAdapter):
    provider_id: ClassVar[str] = "synclabs"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.LIPSYNC})

    def _headers(self) -> dict[str, str]:
        return {"x-api-key": self.credential("api_key")}

    async def test_connection(self) -> None:
        await send(
            self.http(),
            self.provider_id,
            "GET",
            f"{self.base_url()}/v2/models",
            headers=self._headers(),
        )

    async def submit(self, capability: Capability, request: dict[str, Any]) -> JobHandle:
        if capability != Capability.LIPSYNC:
            return await super().submit(capability, request)
        parsed = parse_lipsync(request, provider=self.provider_id)
        video, audio = Path(parsed.video), Path(parsed.audio)
        video_bytes, audio_bytes = await asyncio.gather(
            asyncio.to_thread(video.read_bytes), asyncio.to_thread(audio.read_bytes)
        )
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            f"{self.base_url()}/v2/generate",
            headers=self._headers(),
            data={"model": self.require_model(), "options": json.dumps({"sync_mode": "cut_off"})},
            files={
                "video": (video.name, video_bytes, "video/mp4"),
                "audio": (audio.name, audio_bytes),
            },
        )
        generation = response.json().get("id")
        if not generation:
            raise ProviderError("Sync did not return a generation id", provider=self.provider_id)
        return JobHandle(
            provider=self.provider_id,
            capability=capability,
            external_id=generation,
            model=self.model,
        )

    async def _generation(self, generation_id: str) -> dict[str, Any]:
        response = await send(
            self.http(),
            self.provider_id,
            "GET",
            f"{self.base_url()}/v2/generate/{generation_id}",
            headers=self._headers(),
        )
        return response.json()

    async def poll(self, handle: JobHandle) -> PollResult:
        body = await self._generation(handle.external_id)
        raw = str(body.get("status"))
        status = STATUS.get(raw, RemoteStatus.FAILED)
        if status != RemoteStatus.FAILED:
            return PollResult(status=status)
        return PollResult(
            status=status,
            error_type=ContentRejected.error_type if raw == "REJECTED" else "provider_error",
            error=str(body.get("error") or raw),
        )

    async def fetch_result(self, handle: JobHandle) -> Artifact:
        body = await self._generation(handle.external_id)
        if not body.get("outputUrl"):
            raise ProviderError("The finished generation has no output", provider=self.provider_id)
        return Artifact(
            kind="video", uri=body["outputUrl"], mime_type="video/mp4", meta={"model": self.model}
        )
