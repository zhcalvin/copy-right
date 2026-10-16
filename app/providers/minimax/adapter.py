"""MiniMax speech synthesis (T2A v2).

Checked against https://platform.minimax.cn/docs/api-reference/speech-t2a-http on
2026-10-16 and a live call: POST {base}/v1/t2a_v2 with ``output_format: "url"`` returns a
24-hour audio URL in ``data.audio``. Errors arrive as HTTP 200 with a non-zero
``base_resp.status_code``.
"""

from typing import Any, ClassVar

from app.capabilities.audio import parse_speech
from app.capabilities.base import (
    Artifact,
    AuthError,
    Capability,
    ContentRejected,
    InputInvalid,
    ProviderError,
    QuotaError,
    RateLimited,
)
from app.providers.http import InlineAdapter, send

DEFAULT_VOICE = "male-qn-jingying"
# base_resp.status_code values from the docs.
STATUS_ERRORS: dict[int, type[ProviderError]] = {
    1002: RateLimited,
    1004: AuthError,
    1008: QuotaError,
    1026: ContentRejected,
    1027: ContentRejected,
    1039: RateLimited,
    1042: InputInvalid,
    2013: InputInvalid,
}


class MiniMaxAdapter(InlineAdapter):
    provider_id: ClassVar[str] = "minimax"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.TTS})

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.credential('api_key')}"}

    async def test_connection(self) -> None:
        await send(
            self.http(),
            self.provider_id,
            "GET",
            f"{self.base_url()}/v1/models",
            headers=self._headers(),
        )

    async def run(self, capability: Capability, request: dict[str, Any]) -> Artifact:
        if capability != Capability.TTS:
            return await super().run(capability, request)
        speech = parse_speech(request, provider=self.provider_id)
        response = await send(
            self.http(),
            self.provider_id,
            "POST",
            f"{self.base_url()}/v1/t2a_v2",
            headers=self._headers(),
            json={
                "model": self.require_model(),
                "text": speech.text,
                "stream": False,
                "voice_setting": {"voice_id": speech.voice or DEFAULT_VOICE, "speed": speech.speed},
                "audio_setting": {"format": "mp3", "sample_rate": 32000, "channel": 1},
                "output_format": "url",
            },
        )
        body = response.json()
        self._raise_for_status(body)
        url = (body.get("data") or {}).get("audio")
        if not url:
            raise ProviderError("Speech synthesis returned no audio", provider=self.provider_id)
        extra = body.get("extra_info") or {}
        return Artifact(
            kind="audio",
            uri=url,
            mime_type="audio/mpeg",
            meta={
                "model": self.model,
                "seconds": (extra.get("audio_length") or 0) / 1000,
                "characters": extra.get("usage_characters"),
            },
        )

    def _raise_for_status(self, body: dict[str, Any]) -> None:
        status = body.get("base_resp") or {}
        code = int(status.get("status_code") or 0)
        if code == 0:
            return
        error = STATUS_ERRORS.get(code, ProviderError)
        raise error(f"{code}: {status.get('status_msg', 'error')}", provider=self.provider_id)
