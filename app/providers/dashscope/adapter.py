"""Alibaba Cloud Model Studio (DashScope), via its OpenAI-compatible endpoint.

Verified against https://help.aliyun.com/zh/model-studio/vision on 2026-10-14:
- a shot is sent as ``{"type": "video", "video": [image data URLs], "fps": f}``;
  frame lists need at least 4 images and ``fps`` must be within [0.1, 10];
- ``enable_thinking: false`` is accepted. ``response_format: json_object`` is not used: on
  storyboard prompts it was slower and sometimes aborted mid-reply ("Model output became
  abnormal"), while plain prompting returned complete JSON.
"""

from typing import Any, ClassVar

from app.capabilities.base import Artifact, Capability
from app.capabilities.video_understanding import (
    UnderstandingRequest,
    UnderstandingResult,
    describe_in_chunks,
    instructions,
    parse_request,
    parse_with_retry,
    shot_label,
)
from app.providers.http import InlineAdapter, data_url, send

MIN_FRAMES = 4
FPS_RANGE = (0.1, 10.0)


def _frames(paths: list[str]) -> list[str]:
    urls = [data_url(p) for p in paths]
    if len(urls) >= MIN_FRAMES:
        return urls
    repeat = -(-MIN_FRAMES // len(urls))  # pad short shots by repeating frames in order
    return [url for url in urls for _ in range(repeat)][:MIN_FRAMES]


def _fps(frame_count: int, duration: float) -> float:
    low, high = FPS_RANGE
    return round(min(high, max(low, frame_count / max(duration, 0.01))), 2)


class DashScopeAdapter(InlineAdapter):
    provider_id: ClassVar[str] = "dashscope"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.VIDEO_UNDERSTANDING})

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.credential('api_key')}"}

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
        content: list[dict[str, Any]] = []
        for shot in request.shots:
            frames = _frames(shot.frames)
            content.append({"type": "text", "text": shot_label(shot)})
            content.append(
                {"type": "video", "video": frames, "fps": _fps(len(frames), shot.end - shot.start)}
            )
        return {
            "model": self.require_model(),
            "messages": [
                {"role": "system", "content": instructions(request)},
                {"role": "user", "content": content},
            ],
            "enable_thinking": False,
        }

    async def _describe(self, request: UnderstandingRequest) -> UnderstandingResult:
        payload = self._payload(request)

        async def fetch_text() -> str:
            response = await send(
                self.http(),
                self.provider_id,
                "POST",
                f"{self.base_url()}/chat/completions",
                headers=self._headers(),
                json=payload,
            )
            return _message_text(response.json())

        return await parse_with_retry(
            fetch_text, provider=self.provider_id, expected={s.shot_id for s in request.shots}
        )


def _message_text(body: Any) -> str:
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return ""
    if isinstance(content, list):  # some models return content parts
        return "".join(part.get("text", "") for part in content if isinstance(part, dict))
    return content or ""
