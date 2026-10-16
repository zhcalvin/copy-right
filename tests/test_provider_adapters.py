import json

import httpx
import pytest

from app.capabilities.base import (
    AuthError,
    Capability,
    ContentRejected,
    InputInvalid,
    JobHandle,
    ProviderError,
    ProviderTimeout,
    QuotaError,
    RateLimited,
    RemoteStatus,
)
from app.providers.dashscope.adapter import DashScopeAdapter
from app.providers.gemini.adapter import GeminiAdapter
from app.providers.http import raise_for_response, send

SECRET = "sk-test-abcdefgh1234"
DASHSCOPE = {"api_key": SECRET, "base_url": "https://ds.example/compatible-mode/v1"}
GEMINI = {"api_key": SECRET, "base_url": "https://gem.example/v1beta"}


@pytest.fixture
def frames(tmp_path):
    paths = []
    for i in range(2):
        path = tmp_path / f"f{i}.jpg"
        path.write_bytes(b"\xff\xd8fake-jpeg")
        paths.append(str(path))
    return paths


def _request(frames, count=2):
    return {
        "language": "en",
        "shots": [
            {"shot_id": f"s{i:03d}", "start": i, "end": i + 0.4, "frames": frames}
            for i in range(1, count + 1)
        ],
    }


def _reply(request) -> dict:
    return {
        "characters": [{"character_id": "char_1", "name": "Presenter"}],
        "shots": [{"shot_id": s["shot_id"], "description": "x"} for s in request["shots"]],
    }


def _chat(content: str) -> httpx.Response:
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# -- shared HTTP error mapping -------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"error": {"message": "bad"}}, AuthError),
        (400, {"error": {"status": "INVALID_ARGUMENT", "message": "API_KEY_INVALID"}}, AuthError),
        (429, {"error": {"code": "Throttling.RateQuota"}}, RateLimited),
        (429, {"error": {"message": "Your prepayment credits are depleted"}}, QuotaError),
        (402, {}, QuotaError),
        (400, {"code": "Arrearage", "message": "overdue"}, QuotaError),
        (
            400,
            {"code": "DataInspectionFailed", "message": "data_inspection_failed"},
            ContentRejected,
        ),
        (400, {"error": {"message": "bad fps"}}, InputInvalid),
        (504, {}, ProviderTimeout),
        (500, "oops", ProviderError),
    ],
)
def test_raise_for_response(status, body, error):
    response = (
        httpx.Response(status, json=body)
        if isinstance(body, dict)
        else httpx.Response(status, text=body)
    )
    with pytest.raises(error):
        raise_for_response(response, "p")
    raise_for_response(httpx.Response(200), "p")


async def test_send_retries_dropped_connections_once():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            raise httpx.RemoteProtocolError("dropped", request=request)
        return httpx.Response(200)

    assert (await send(_client(handler), "p", "GET", "https://x.example")).status_code == 200
    assert len(calls) == 2

    def always_down(request):
        raise httpx.ConnectError("down", request=request)

    with pytest.raises(ProviderError, match="Cannot reach"):
        await send(_client(always_down), "p", "GET", "https://x.example")

    def slow(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(ProviderTimeout):
        await send(_client(slow), "p", "GET", "https://x.example")


# -- DashScope -------------------------------------------------------------------------


async def test_dashscope_describes_shots_as_frame_lists(frames):
    request = _request(frames)
    seen: list[dict] = []

    def handler(http_request):
        seen.append(json.loads(http_request.content))
        assert http_request.headers["authorization"] == f"Bearer {SECRET}"
        assert http_request.url.path.endswith("/chat/completions")
        return _chat(json.dumps(_reply(request)))

    adapter = DashScopeAdapter(credentials=DASHSCOPE, model="qwen-test", client=_client(handler))
    handle = await adapter.submit(Capability.VIDEO_UNDERSTANDING, request)
    assert (await adapter.poll(handle)).status == RemoteStatus.SUCCEEDED
    artifact = await adapter.fetch_result(handle)

    assert [s["shot_id"] for s in artifact.data["shots"]] == ["s001", "s002"]
    payload = seen[0]
    assert payload["model"] == "qwen-test" and payload["enable_thinking"] is False
    video = payload["messages"][1]["content"][1]
    assert video["type"] == "video"
    assert len(video["video"]) == 4  # 2 frames padded to the provider minimum
    assert video["video"][0].startswith("data:image/jpeg;base64,")
    assert video["fps"] == 10.0  # 4 frames in 0.4 s, clamped to the provider maximum


async def test_dashscope_retries_a_bad_reply_once(frames):
    request = _request(frames)
    replies = iter([_chat("Sorry, here you go: {"), _chat(json.dumps(_reply(request)))])
    adapter = DashScopeAdapter(
        credentials=DASHSCOPE, model="m", client=_client(lambda r: next(replies))
    )
    handle = await adapter.submit(Capability.VIDEO_UNDERSTANDING, request)
    assert len((await adapter.fetch_result(handle)).data["shots"]) == 2


async def test_dashscope_errors(frames):
    def rejected(request):
        return httpx.Response(400, json={"code": "DataInspectionFailed", "message": "x"})

    adapter = DashScopeAdapter(credentials=DASHSCOPE, model="m", client=_client(rejected))
    with pytest.raises(ContentRejected):
        await adapter.submit(Capability.VIDEO_UNDERSTANDING, _request(frames))

    no_key = DashScopeAdapter(credentials={"base_url": "https://x"}, model="m")
    with pytest.raises(AuthError, match="api_key"):
        await no_key.submit(Capability.VIDEO_UNDERSTANDING, _request(frames))

    no_model = DashScopeAdapter(credentials=DASHSCOPE, client=_client(rejected))
    with pytest.raises(InputInvalid, match="No model"):
        await no_model.submit(Capability.VIDEO_UNDERSTANDING, _request(frames))

    with pytest.raises(InputInvalid, match="does not implement"):
        await no_model.submit(Capability.TTS, {})

    with pytest.raises(InputInvalid, match="Bad video understanding request"):
        await no_model.submit(Capability.VIDEO_UNDERSTANDING, {"shots": []})


async def test_lost_result_after_restart_fails_cleanly():
    adapter = DashScopeAdapter(credentials=DASHSCOPE, model="m")
    handle = JobHandle(
        provider="dashscope", capability=Capability.VIDEO_UNDERSTANDING, external_id="gone"
    )
    result = await adapter.poll(handle)
    assert result.status == RemoteStatus.FAILED and "restarted" in result.error
    with pytest.raises(ProviderError):
        await adapter.fetch_result(handle)


async def test_dashscope_connection_check():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": []})

    await DashScopeAdapter(credentials=DASHSCOPE, client=_client(handler)).test_connection()
    assert seen[0].url.path == "/compatible-mode/v1/models"


async def test_dashscope_reads_content_parts(frames):
    request = _request(frames)
    parts = [{"type": "text", "text": json.dumps(_reply(request))}]
    response = httpx.Response(200, json={"choices": [{"message": {"content": parts}}]})
    adapter = DashScopeAdapter(credentials=DASHSCOPE, model="m", client=_client(lambda r: response))
    handle = await adapter.submit(Capability.VIDEO_UNDERSTANDING, request)
    assert (await adapter.fetch_result(handle)).data["characters"][0]["name"] == "Presenter"


# -- Gemini ----------------------------------------------------------------------------


def _gemini(text: str, **candidate) -> httpx.Response:
    body = {"candidates": [{"content": {"parts": [{"text": text}]}, **candidate}]}
    return httpx.Response(200, json=body)


async def test_gemini_sends_inline_frames(frames):
    request = _request(frames)
    seen: list[httpx.Request] = []

    def handler(http_request):
        seen.append(http_request)
        return _gemini(json.dumps(_reply(request)))

    adapter = GeminiAdapter(credentials=GEMINI, model="gemini-test", client=_client(handler))
    handle = await adapter.submit(Capability.VIDEO_UNDERSTANDING, request)
    assert len((await adapter.fetch_result(handle)).data["shots"]) == 2

    http_request = seen[0]
    assert http_request.url.path == "/v1beta/models/gemini-test:generateContent"
    assert http_request.headers["x-goog-api-key"] == SECRET
    payload = json.loads(http_request.content)
    parts = payload["contents"][0]["parts"]
    assert parts[0]["text"].startswith("Shot s001")
    assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
    assert payload["generationConfig"]["response_mime_type"] == "application/json"


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}),
        _gemini("", finishReason="PROHIBITED_CONTENT"),
    ],
)
async def test_gemini_blocks_are_content_rejected(frames, response):
    adapter = GeminiAdapter(credentials=GEMINI, model="m", client=_client(lambda r: response))
    with pytest.raises(ContentRejected):
        await adapter.submit(Capability.VIDEO_UNDERSTANDING, _request(frames))


async def test_gemini_skips_thought_parts_and_empty_candidates(frames):
    request = _request(frames)
    thought = {"text": "thinking...", "thought": True}
    answer = {"text": json.dumps(_reply(request))}
    body = {"candidates": [{"content": {"parts": [thought, answer]}}]}
    adapter = GeminiAdapter(
        credentials=GEMINI, model="m", client=_client(lambda r: httpx.Response(200, json=body))
    )
    handle = await adapter.submit(Capability.VIDEO_UNDERSTANDING, request)
    assert len((await adapter.fetch_result(handle)).data["shots"]) == 2

    empty = GeminiAdapter(
        credentials=GEMINI,
        model="m",
        client=_client(lambda r: httpx.Response(200, json={"candidates": []})),
    )
    with pytest.raises(InputInvalid):
        await empty.submit(Capability.VIDEO_UNDERSTANDING, request)


async def test_gemini_connection_check():
    seen = []
    client = _client(lambda r: seen.append(r) or httpx.Response(200, json={}))
    await GeminiAdapter(credentials=GEMINI, client=client).test_connection()
    assert seen[0].url.path == "/v1beta/models"
    with pytest.raises(InputInvalid):
        await GeminiAdapter(credentials=GEMINI, client=client).submit(Capability.TTS, {})


async def test_gemini_text():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return _gemini('{"summary": "fine"}')

    adapter = GeminiAdapter(credentials=GEMINI, model="gemini-test", client=_client(handler))
    handle = await adapter.submit(Capability.LLM_TEXT, {"system": "s", "prompt": "p"})
    artifact = await adapter.fetch_result(handle)
    assert artifact.data == {"text": '{"summary": "fine"}'}
    assert seen["body"]["system_instruction"] == {"parts": [{"text": "s"}]}
    assert seen["body"]["generationConfig"] == {"response_mime_type": "application/json"}
