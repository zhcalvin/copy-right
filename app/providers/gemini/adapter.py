"""Google Gemini API, via ``models.generateContent``.

Checked against https://ai.google.dev/api/generate-content on 2026-10-14: frames go in
as ``inline_data`` parts and ``generationConfig.response_mime_type`` asks for JSON.
Not yet exercised against a live key.
"""

import base64
import mimetypes
from pathlib import Path
from typing import Any, ClassVar

from app.capabilities.base import Artifact, Capability, ContentRejected
from app.capabilities.video_understanding import (
    UnderstandingRequest,
    UnderstandingResult,
    describe_in_chunks,
    instructions,
    parse_request,
    parse_with_retry,
    shot_label,
)
from app.providers.http import InlineAdapter, send

BLOCKED_FINISH_REASONS = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "IMAGE_SAFETY"})


def _inline(path: str) -> dict[str, Any]:
    file = Path(path)
    mime = mimetypes.guess_type(file.name)[0] or "image/jpeg"
    return {
        "inline_data": {"mime_type": mime, "data": base64.b64encode(file.read_bytes()).decode()}
    }


class GeminiAdapter(InlineAdapter):
    provider_id: ClassVar[str] = "gemini"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.VIDEO_UNDERSTANDING})

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.credential("api_key")}

    async def test_connection(self) -> None:
        await send(
            self.http(),
            self.provider_id,
            "GET",
            f"{self.base_url()}/models",
            headers=self._headers(),
        )

    async def run(self, capability: Capability, request: dict[str, Any]) -> Artifact:
        if capability != Capability.VIDEO_UNDERSTANDING:
            return await super().run(capability, request)
        parsed = parse_request(request, provider=self.provider_id)
        result = await describe_in_chunks(parsed, self._describe)
        return Artifact(kind="json", data=result.model_dump(), meta={"model": self.model})

    def _payload(self, request: UnderstandingRequest) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for shot in request.shots:
            parts.append({"text": shot_label(shot)})
            parts.extend(_inline(frame) for frame in shot.frames)
        return {
            "system_instruction": {"parts": [{"text": instructions(request)}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"response_mime_type": "application/json"},
        }

    async def _describe(self, request: UnderstandingRequest) -> UnderstandingResult:
        url = f"{self.base_url()}/models/{self.require_model()}:generateContent"
        payload = self._payload(request)

        async def fetch_text() -> str:
            response = await send(
                self.http(), self.provider_id, "POST", url, headers=self._headers(), json=payload
            )
            return self._reply_text(response.json())

        return await parse_with_retry(
            fetch_text, provider=self.provider_id, expected={s.shot_id for s in request.shots}
        )

    def _reply_text(self, body: dict[str, Any]) -> str:
        block = (body.get("promptFeedback") or {}).get("blockReason")
        if block:
            raise ContentRejected(f"Request blocked: {block}", provider=self.provider_id)
        candidates = body.get("candidates") or []
        if not candidates:
            return ""
        first = candidates[0]
        if first.get("finishReason") in BLOCKED_FINISH_REASONS:
            raise ContentRejected(
                f"Reply blocked: {first['finishReason']}", provider=self.provider_id
            )
        parts = (first.get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts if not p.get("thought"))
