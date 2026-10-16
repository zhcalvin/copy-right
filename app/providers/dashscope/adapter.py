"""Alibaba Cloud Model Studio (DashScope).

``video_understanding`` uses the OpenAI-compatible endpoint; ``image_edit``,
``video_edit_v2v`` and ``image_to_video`` use the native API (see :mod:`app.providers.dashscope.native`).

Video understanding verified against https://help.aliyun.com/zh/model-studio/vision on 2026-10-14:
- a shot is sent as ``{"type": "video", "video": [image data URLs], "fps": f}``;
  frame lists need at least 4 images and ``fps`` must be within [0.1, 10];
- ``enable_thinking: false`` is accepted. ``response_format: json_object`` is not used: on
  storyboard prompts it was slower and sometimes aborted mid-reply ("Model output became
  abnormal"), while plain prompting returned complete JSON.
"""

import math
from pathlib import Path
from typing import Any, ClassVar

from app.capabilities import llm_text
from app.capabilities.base import (
    Artifact,
    Capability,
    InputInvalid,
    JobHandle,
    PollResult,
)
from app.capabilities.generation import (
    ImageEditRequest,
    ImageToVideoRequest,
    VideoEditRequest,
    parse,
)
from app.capabilities.video_understanding import (
    UnderstandingRequest,
    UnderstandingResult,
    describe_in_chunks,
    instructions,
    parse_request,
    parse_with_retry,
    shot_label,
)
from app.providers.dashscope import native
from app.providers.http import InlineAdapter, data_url, send
from app.tools.media import MediaError, probe

MIN_FRAMES = 4
FPS_RANGE = (0.1, 10.0)
# Wan Animate (swap = animate-mix, motion = animate-move) accepts 2–30 s clips;
# Wan video edit accepts 2–10 s.
CLIP_DURATION_S = {"swap": (2.0, 30.0), "motion": (2.0, 30.0), "edit": (2.0, 10.0)}
ANIMATE_MODES = {"standard": "wan-std", "pro": "wan-pro"}
EDIT_RESOLUTION = {"standard": "720P", "pro": "1080P"}
# Wan image-to-video takes whole seconds; shorter shots are generated longer and trimmed.
I2V_DURATION_S = (2, 15)
ASYNC_CAPABILITIES = frozenset({Capability.VIDEO_EDIT_V2V, Capability.IMAGE_TO_VIDEO})


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
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {
            Capability.VIDEO_UNDERSTANDING,
            Capability.LLM_TEXT,
            Capability.IMAGE_EDIT,
            Capability.VIDEO_EDIT_V2V,
            Capability.IMAGE_TO_VIDEO,
        }
    )

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.credential('api_key')}"}

    def _native(self) -> str:
        return native.native_base(self.base_url())

    async def test_connection(self) -> None:
        await send(
            self.http(),
            self.provider_id,
            "GET",
            f"{self.base_url()}/models",
            headers=self._headers(),
        )

    async def run(self, capability: Capability, request: dict[str, Any]) -> Artifact:
        if capability == Capability.LLM_TEXT:
            return await self._text(llm_text.parse_request(request, provider=self.provider_id))
        if capability == Capability.IMAGE_EDIT:
            return await self._edit_image(
                parse(ImageEditRequest, request, provider=self.provider_id)
            )
        if capability != Capability.VIDEO_UNDERSTANDING:
            return await super().run(capability, request)
        parsed = parse_request(request, provider=self.provider_id)
        result = await describe_in_chunks(parsed, self._describe)
        return Artifact(kind="json", data=result.model_dump(), meta={"model": self.model})

    # -- async tasks (video_edit_v2v, image_to_video) ------------------------------------------------

    async def submit(self, capability: Capability, request: dict[str, Any]) -> JobHandle:
        if capability not in ASYNC_CAPABILITIES:
            return await super().submit(capability, request)
        if capability == Capability.IMAGE_TO_VIDEO:
            task_id = await self._start_i2v(
                parse(ImageToVideoRequest, request, provider=self.provider_id)
            )
        else:
            task_id = await self._start_video_edit(
                parse(VideoEditRequest, request, provider=self.provider_id)
            )
        return JobHandle(
            provider=self.provider_id, capability=capability, external_id=task_id, model=self.model
        )

    async def poll(self, handle: JobHandle) -> PollResult:
        if handle.capability not in ASYNC_CAPABILITIES:
            return await super().poll(handle)
        return native.task_poll_result(await self._task(handle.external_id))

    async def fetch_result(self, handle: JobHandle) -> Artifact:
        if handle.capability not in ASYNC_CAPABILITIES:
            return await super().fetch_result(handle)
        body = await self._task(handle.external_id)
        return Artifact(
            kind="video",
            uri=native.task_video_url(body),
            mime_type="video/mp4",
            meta={"model": self.model, "task_id": handle.external_id, "usage": body.get("usage")},
        )

    async def _task(self, task_id: str) -> dict[str, Any]:
        response = await send(
            self.http(),
            self.provider_id,
            "GET",
            f"{self._native()}/tasks/{task_id}",
            headers=self._headers(),
        )
        return response.json()

    async def _start_video_edit(self, request: VideoEditRequest) -> str:
        model = self.require_model()
        video = Path(request.video)
        try:
            duration = probe(video).duration
        except MediaError as exc:
            raise InputInvalid(
                f"Cannot read the source clip: {exc}", provider=self.provider_id
            ) from exc
        low, high = CLIP_DURATION_S[request.mode]
        if not low <= duration <= high:
            raise InputInvalid(
                f"Video {request.mode} needs a {low:.0f}–{high:.0f} s clip; "
                f"this one is {duration:.1f} s",
                provider=self.provider_id,
            )
        headers = self._headers()
        video_url = await native.upload_temporary(
            self.http(), self._native(), headers, model, video
        )
        if request.mode == "edit":
            path, body = self._edit_body(model, request, video_url)
        else:
            image_url = await native.upload_temporary(
                self.http(), self._native(), headers, model, Path(request.reference_images[0])
            )
            path = "image2video/video-synthesis"
            body = {
                "model": model,
                "input": {"image_url": image_url, "video_url": video_url},
                "parameters": {"mode": ANIMATE_MODES[request.quality]},
            }
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            f"{self._native()}/services/aigc/{path}",
            headers={
                **headers,
                "X-DashScope-Async": "enable",
                "X-DashScope-OssResourceResolve": "enable",
            },
            json=body,
        )
        task_id = (response.json().get("output") or {}).get("task_id")
        if not task_id:
            raise InputInvalid("The provider did not return a task id", provider=self.provider_id)
        return task_id

    @staticmethod
    def _edit_body(
        model: str, request: VideoEditRequest, video_url: str
    ) -> tuple[str, dict[str, Any]]:
        media = [
            {"type": "video", "url": video_url},
            *({"type": "reference_image", "url": data_url(p)} for p in request.reference_images),
        ]
        return "video-generation/video-synthesis", {
            "model": model,
            "input": {"prompt": request.prompt, "media": media},
            "parameters": {
                "resolution": EDIT_RESOLUTION[request.quality],
                "audio_setting": "origin",
                "prompt_extend": True,
                "watermark": False,
            },
        }

    async def _start_i2v(self, request: ImageToVideoRequest) -> str:
        low, high = I2V_DURATION_S
        duration = min(high, max(low, math.ceil(request.duration)))
        text_input: dict[str, Any] = {
            "img_url": data_url(request.first_frame),
            "prompt": request.prompt,
        }
        if request.negative_prompt:
            text_input["negative_prompt"] = request.negative_prompt
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            f"{self._native()}/services/aigc/video-generation/video-synthesis",
            headers={**self._headers(), "X-DashScope-Async": "enable"},
            json={
                "model": self.require_model(),
                "input": text_input,
                "parameters": {
                    "resolution": request.resolution,
                    "duration": duration,
                    "prompt_extend": True,
                    "watermark": False,
                },
            },
        )
        task_id = (response.json().get("output") or {}).get("task_id")
        if not task_id:
            raise InputInvalid("The provider did not return a task id", provider=self.provider_id)
        return task_id

    # -- text (synchronous) -----------------------------------------------------------

    async def _text(self, request: llm_text.TextRequest) -> Artifact:
        messages = [
            *([{"role": "system", "content": request.system}] if request.system else []),
            {"role": "user", "content": request.prompt},
        ]
        payload: dict[str, Any] = {
            "model": self.require_model(),
            "messages": messages,
            "enable_thinking": False,
        }
        if request.json_output:
            payload["response_format"] = {"type": "json_object"}
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            f"{self.base_url()}/chat/completions",
            headers=self._headers(),
            json=payload,
        )
        body = response.json()
        return llm_text.text_artifact(
            _message_text(body), model=self.model, usage=body.get("usage")
        )

    # -- image edit (synchronous) -----------------------------------------------------

    async def _edit_image(self, request: ImageEditRequest) -> Artifact:
        parameters: dict[str, Any] = {"n": request.n, "watermark": False}
        if request.size:
            parameters["size"] = request.size
        content = [*({"image": data_url(p)} for p in request.images), {"text": request.prompt}]
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            f"{self._native()}/services/aigc/multimodal-generation/generation",
            headers=self._headers(),
            json={
                "model": self.require_model(),
                "input": {"messages": [{"role": "user", "content": content}]},
                "parameters": parameters,
            },
        )
        urls = native.edited_image_urls(response.json())
        return Artifact(
            kind="image",
            uri=urls[0],
            mime_type="image/png",
            meta={"model": self.model, "urls": urls},
        )

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
