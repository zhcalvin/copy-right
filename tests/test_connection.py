import httpx
import pytest

from app.providers.connection import check_provider, run_http_check
from app.providers.registry import ConnectionTestSpec, get_registry

SECRET = "sk-live-abcdefgh9876"


def _client(status: int, seen: list[httpx.Request] | None = None) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json={})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_ok_and_templates_are_filled():
    seen: list[httpx.Request] = []
    spec = get_registry().provider("kling")
    creds = {"api_key": SECRET, "base_url": "https://kling.example"}
    result = await check_provider(spec, creds, _client(200, seen))
    assert result.status == "ok"
    request = seen[0]
    assert request.url.host == "kling.example"
    assert request.url.path == "/account/costs"
    assert request.headers["authorization"] == f"Bearer {SECRET}"
    start, end = int(request.url.params["start_time"]), int(request.url.params["end_time"])
    assert end - start == 24 * 60 * 60 * 1000


@pytest.mark.parametrize(
    ("provider", "status", "error_type"),
    [
        ("elevenlabs", 401, "auth_error"),
        ("runway", 403, "auth_error"),
        ("gemini", 400, "auth_error"),
        ("synclabs", 429, "quota_error"),
        ("minimax", 402, "quota_error"),
        ("elevenlabs", 500, "provider_error"),
    ],
)
async def test_status_mapping(provider, status, error_type):
    spec = get_registry().provider(provider)
    creds = {"api_key": SECRET, "base_url": "https://x.example"}
    result = await check_provider(spec, creds, _client(status))
    assert result.status == "failed"
    assert result.error_type == error_type
    assert SECRET not in (result.message or "")


async def test_status_override_can_mean_ok():
    spec = get_registry().provider("fal")
    assert (await check_provider(spec, {"api_key": SECRET}, _client(403))).status == "ok"


async def test_missing_credentials_is_auth_error():
    spec = get_registry().provider("gemini")
    result = await check_provider(spec, {}, _client(200))
    assert result.error_type == "auth_error"
    assert "api_key" in result.message


async def test_unsupported_without_check():
    result = await check_provider(get_registry().provider("volcengine"), {"api_key": SECRET})
    assert result.status == "unsupported"


async def test_mock_uses_adapter():
    assert (await check_provider(get_registry().provider("mock"), {})).status == "ok"


async def test_timeout_and_network_errors():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request):
        raise httpx.ConnectError("refused", request=request)

    spec = ConnectionTestSpec(url="https://x.example/{api_key}")
    for handler, error_type in ((timeout, "provider_timeout"), (refused, "provider_error")):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        result = await check_provider(
            get_registry().provider("gemini").model_copy(update={"connection_test": spec}),
            {"api_key": SECRET},
            client,
        )
        assert result.error_type == error_type
        assert SECRET not in result.message


async def test_run_http_check_owns_its_client(monkeypatch):
    created: list[httpx.AsyncClient] = []
    real = httpx.AsyncClient

    def factory(**kwargs):
        client = real(transport=httpx.MockTransport(lambda r: httpx.Response(200)), **kwargs)
        created.append(client)
        return client

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    await run_http_check("x", ConnectionTestSpec(url="https://x.example"), {})
    assert created and created[0].is_closed
