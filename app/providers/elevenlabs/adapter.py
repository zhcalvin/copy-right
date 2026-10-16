"""ElevenLabs text-to-speech.

Checked against https://elevenlabs.io/docs/api-reference/text-to-speech/convert on
2026-10-16 (not yet run against a live key): POST /v1/text-to-speech/{voice_id} returns
the audio bytes, which are saved locally.
"""

import uuid
from typing import Any, ClassVar

from app.capabilities.audio import parse_speech
from app.capabilities.base import Artifact, Capability
from app.providers.http import InlineAdapter, result_file, send

DEFAULT_VOICE = "JBFqnCBsd6RMkjVDRZzb"


class ElevenLabsAdapter(InlineAdapter):
    provider_id: ClassVar[str] = "elevenlabs"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.TTS})

    def _headers(self) -> dict[str, str]:
        return {"xi-api-key": self.credential("api_key")}

    async def test_connection(self) -> None:
        await send(
            self.http(),
            self.provider_id,
            "GET",
            f"{self.base_url()}/v1/user",
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
            f"{self.base_url()}/v1/text-to-speech/{speech.voice or DEFAULT_VOICE}",
            params={"output_format": "mp3_44100_128"},
            headers=self._headers(),
            json={
                "text": speech.text,
                "model_id": self.require_model(),
                "voice_settings": {"speed": speech.speed},
            },
        )
        out = result_file(self.provider_id, f"{uuid.uuid4().hex}.mp3")
        out.write_bytes(response.content)
        return Artifact(
            kind="audio", uri=str(out), mime_type="audio/mpeg", meta={"model": self.model}
        )
