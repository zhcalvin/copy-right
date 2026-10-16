"""Google Gemini API: ``generateContent`` for understanding, text, image edits and speech;
``predictLongRunning`` (Veo) for image-to-video.

Checked against https://ai.google.dev/api/generate-content (2026-10-14) and
https://ai.google.dev/gemini-api/docs/veo (2026-10-16); the model list was checked with a
live key, but generation calls are not exercised yet (the test account has no credit).
Images and speech come back inline and are saved locally; Veo videos need the API key to
download, so the adapter downloads them itself.
"""

import base64
import mimetypes
import uuid
from pathlib import Path
from typing import Any, ClassVar

from app.capabilities import llm_text
from app.capabilities.audio import SpeechRequest, parse_speech
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
from app.capabilities.generation import ImageEditRequest, ImageToVideoRequest, parse
from app.capabilities.video_understanding import (
    UnderstandingRequest,
    UnderstandingResult,
    describe_in_chunks,
    instructions,
    parse_request,
    parse_with_retry,
    shot_label,
)
from app.providers.http import InlineAdapter, pcm_to_wav, result_file, send

VEO_SECONDS = (4, 6, 8)
DEFAULT_VOICE = "Kore"
BLOCKED_FINISH_REASONS = frozenset({"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "IMAGE_SAFETY"})


def _inline(path: str) -> dict[str, Any]:
    file = Path(path)
    mime = mimetypes.guess_type(file.name)[0] or "image/jpeg"
    return {
        "inline_data": {"mime_type": mime, "data": base64.b64encode(file.read_bytes()).decode()}
    }


class GeminiAdapter(InlineAdapter):
    provider_id: ClassVar[str] = "gemini"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {
            Capability.VIDEO_UNDERSTANDING,
            Capability.LLM_TEXT,
            Capability.IMAGE_EDIT,
            Capability.IMAGE_TO_VIDEO,
            Capability.TTS,
        }
    )

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
        if capability == Capability.IMAGE_EDIT:
            return await self._edit_image(
                parse(ImageEditRequest, request, provider=self.provider_id)
            )
        if capability == Capability.TTS:
            return await self._speak(parse_speech(request, provider=self.provider_id))
        if capability == Capability.LLM_TEXT:
            return await self._text(llm_text.parse_request(request, provider=self.provider_id))
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

    async def _text(self, request: llm_text.TextRequest) -> Artifact:
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}]
        }
        if request.system:
            payload["system_instruction"] = {"parts": [{"text": request.system}]}
        if request.json_output:
            payload["generationConfig"] = {"response_mime_type": "application/json"}
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            f"{self.base_url()}/models/{self.require_model()}:generateContent",
            headers=self._headers(),
            json=payload,
        )
        return llm_text.text_artifact(self._reply_text(response.json()), model=self.model)

    async def _generate(self, payload: dict[str, Any]) -> dict[str, Any]:
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            f"{self.base_url()}/models/{self.require_model()}:generateContent",
            headers=self._headers(),
            json=payload,
        )
        body = response.json()
        self._reply_text(body)  # raises ContentRejected on a blocked reply
        return body

    def _inline_parts(self, body: dict[str, Any]) -> list[dict[str, Any]]:
        parts = (((body.get("candidates") or [{}])[0].get("content") or {}).get("parts")) or []
        return [
            p.get("inlineData") or p.get("inline_data")
            for p in parts
            if p.get("inlineData") or p.get("inline_data")
        ]

    async def _edit_image(self, request: ImageEditRequest) -> Artifact:
        parts = [*(_inline(p) for p in request.images), {"text": request.prompt}]
        body = await self._generate(
            {
                "contents": [{"role": "user", "parts": parts}],
                "generationConfig": {"responseModalities": ["IMAGE"]},
            }
        )
        images = self._inline_parts(body)
        if not images:
            raise InputInvalid("Gemini returned no image", provider=self.provider_id)
        mime = images[0].get("mimeType") or images[0].get("mime_type") or "image/png"
        out = result_file(
            self.provider_id, f"{uuid.uuid4().hex}{mimetypes.guess_extension(mime) or '.png'}"
        )
        out.write_bytes(base64.b64decode(images[0]["data"]))
        return Artifact(kind="image", uri=str(out), mime_type=mime, meta={"model": self.model})

    async def _speak(self, request: SpeechRequest) -> Artifact:
        voice = {"prebuiltVoiceConfig": {"voiceName": request.voice or DEFAULT_VOICE}}
        body = await self._generate(
            {
                "contents": [{"role": "user", "parts": [{"text": request.text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {"voiceConfig": voice},
                },
            }
        )
        audio = self._inline_parts(body)
        if not audio:
            raise InputInvalid("Gemini returned no audio", provider=self.provider_id)
        out = pcm_to_wav(
            base64.b64decode(audio[0]["data"]),
            result_file(self.provider_id, f"{uuid.uuid4().hex}.wav"),
        )
        return Artifact(
            kind="audio", uri=str(out), mime_type="audio/wav", meta={"model": self.model}
        )

    # -- Veo (asynchronous) ------------------------------------------------------------

    async def submit(self, capability: Capability, request: dict[str, Any]) -> JobHandle:
        if capability != Capability.IMAGE_TO_VIDEO:
            return await super().submit(capability, request)
        parsed = parse(ImageToVideoRequest, request, provider=self.provider_id)
        seconds = next((s for s in VEO_SECONDS if s >= parsed.duration), VEO_SECONDS[-1])
        resolution = "1080p" if parsed.resolution == "1080P" else "720p"
        if resolution == "1080p":
            seconds = VEO_SECONDS[-1]  # 1080p is 8 s only
        image = _inline(parsed.first_frame)["inline_data"]
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            f"{self.base_url()}/models/{self.require_model()}:predictLongRunning",
            headers=self._headers(),
            json={
                "instances": [
                    {
                        "prompt": parsed.prompt,
                        "image": {
                            "inlineData": {"mimeType": image["mime_type"], "data": image["data"]}
                        },
                    }
                ],
                "parameters": {
                    "aspectRatio": "16:9",
                    "durationSeconds": str(seconds),
                    "resolution": resolution,
                    "personGeneration": "allow_adult",
                },
            },
        )
        name = response.json().get("name")
        if not name:
            raise ProviderError("Veo did not return an operation", provider=self.provider_id)
        return JobHandle(
            provider=self.provider_id, capability=capability, external_id=name, model=self.model
        )

    async def _operation(self, name: str) -> dict[str, Any]:
        response = await send(
            self.http(),
            self.provider_id,
            "GET",
            f"{self.base_url()}/{name}",
            headers=self._headers(),
        )
        return response.json()

    async def poll(self, handle: JobHandle) -> PollResult:
        if handle.capability != Capability.IMAGE_TO_VIDEO:
            return await super().poll(handle)
        operation = await self._operation(handle.external_id)
        if not operation.get("done"):
            return PollResult(status=RemoteStatus.RUNNING)
        if operation.get("error"):
            error = operation["error"]
            return PollResult(
                status=RemoteStatus.FAILED,
                error_type="provider_error",
                error=str(error.get("message", error)),
            )
        result = (operation.get("response") or {}).get("generateVideoResponse") or {}
        if result.get("raiMediaFilteredReasons"):
            reasons = "; ".join(result["raiMediaFilteredReasons"])
            return PollResult(
                status=RemoteStatus.FAILED, error_type=ContentRejected.error_type, error=reasons
            )
        return PollResult(status=RemoteStatus.SUCCEEDED)

    async def fetch_result(self, handle: JobHandle) -> Artifact:
        if handle.capability != Capability.IMAGE_TO_VIDEO:
            return await super().fetch_result(handle)
        operation = await self._operation(handle.external_id)
        samples = ((operation.get("response") or {}).get("generateVideoResponse") or {}).get(
            "generatedSamples"
        ) or []
        uri = ((samples[0] if samples else {}).get("video") or {}).get("uri")
        if not uri:
            raise ProviderError("The finished operation has no video", provider=self.provider_id)
        response = await send(
            self.http(),
            self.provider_id,
            "GET",
            uri,
            headers=self._headers(),
            follow_redirects=True,
        )
        out = result_file(self.provider_id, f"{uuid.uuid4().hex}.mp4")
        out.write_bytes(response.content)
        return Artifact(
            kind="video", uri=str(out), mime_type="video/mp4", meta={"model": self.model}
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
