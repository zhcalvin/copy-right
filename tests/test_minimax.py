import json

import httpx
import pytest

from app.capabilities.base import AuthError, Capability, RateLimited
from app.providers.minimax.adapter import MiniMaxAdapter

CREDS = {"api_key": "mm-secret-1234", "base_url": "https://mm.example"}


def _adapter(handler) -> MiniMaxAdapter:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return MiniMaxAdapter(credentials=CREDS, model="speech-2.8-hd", client=client)


async def test_speech_asks_for_a_url():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "data": {"audio": "https://cdn/a.mp3", "status": 2},
                "extra_info": {"audio_length": 1800, "usage_characters": 20},
                "base_resp": {"status_code": 0, "status_msg": "success"},
            },
        )

    adapter = _adapter(handler)
    handle = await adapter.submit(Capability.TTS, {"text": "你好", "voice": "presenter_male"})
    artifact = await adapter.fetch_result(handle)
    assert artifact.uri == "https://cdn/a.mp3" and artifact.meta["seconds"] == 1.8
    assert seen["url"] == "https://mm.example/v1/t2a_v2"
    assert seen["body"]["output_format"] == "url"
    assert seen["body"]["voice_setting"]["voice_id"] == "presenter_male"


@pytest.mark.parametrize(("code", "error"), [(1004, AuthError), (1002, RateLimited)])
async def test_status_codes_map_to_errors(code, error):
    def handler(request):
        return httpx.Response(200, json={"base_resp": {"status_code": code, "status_msg": "no"}})

    with pytest.raises(error):
        await _adapter(handler).submit(Capability.TTS, {"text": "hi"})


async def test_connection_and_unsupported():
    adapter = _adapter(lambda request: httpx.Response(200, json={"data": []}))
    await adapter.test_connection()
    with pytest.raises(Exception, match="does not implement"):
        await adapter.submit(Capability.LIPSYNC, {})
