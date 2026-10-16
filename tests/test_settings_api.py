import pytest

from app.capabilities.base import Capability
from app.providers.registry import get_registry

SECRET = "sk-wizard-1234567890qrst"


def _preset(preset_id: str) -> list[dict]:
    preset = get_registry().presets[preset_id]
    return [{"capability": c, "provider": p, "model": None} for c, p in preset.capabilities.items()]


def test_settings_start_empty(client):
    body = client.get("/api/settings").json()
    assert body == {
        "configured": False,
        "capabilities": [],
        "storage": "provider_upload",
        "key_storage": "file",
    }


@pytest.mark.parametrize("preset_id", ["cn", "global", "demo"])
def test_saving_a_preset_completes_setup(client, preset_id):
    saved = client.put("/api/settings/capabilities", json=_preset(preset_id))
    assert saved.status_code == 200, saved.text
    assert client.get("/api/setup/status").json() == {"configured": True}
    caps = client.get("/api/settings").json()["capabilities"]
    assert [c["capability"] for c in caps] == list(Capability)


def test_optional_capability_can_be_disabled(client):
    selections = [
        {**s, "enabled": False} if s["capability"] == "lipsync" else s for s in _preset("cn")
    ]
    saved = {
        c["capability"]: c for c in client.put("/api/settings/capabilities", json=selections).json()
    }
    assert saved["lipsync"] == {
        "capability": "lipsync",
        "provider": None,
        "model": None,
        "enabled": False,
    }


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"capability": "llm_text", "provider": None}, "is required"),
        ({"capability": "tts", "provider": "runway"}, "does not support"),
        ({"capability": "tts", "provider": "nope"}, "unknown provider"),
        ({"capability": "tts", "provider": "mock", "model": "not-a-model"}, "has no model"),
    ],
)
def test_invalid_selections_are_rejected(client, change, message):
    selections = [
        {**s, **change} if s["capability"] == change["capability"] else s for s in _preset("demo")
    ]
    response = client.put("/api/settings/capabilities", json=selections)
    assert response.status_code == 422
    assert message in response.json()["detail"]


def test_missing_and_duplicate_capabilities_are_rejected(client):
    selections = _preset("demo")
    response = client.put("/api/settings/capabilities", json=[*selections[:-1], selections[0]])
    assert response.status_code == 422
    assert "missing" in response.json()["detail"] and "duplicated" in response.json()["detail"]


def test_free_text_model_allowed_when_registry_lists_none(client):
    selections = [
        {**s, "provider": "dashscope", "model": "  asr-custom  "} if s["capability"] == "asr" else s
        for s in _preset("cn")
    ]
    saved = {
        c["capability"]: c for c in client.put("/api/settings/capabilities", json=selections).json()
    }
    assert saved["asr"]["model"] == "asr-custom"


def test_storage_setting(client):
    assert client.put("/api/settings/storage", json={"storage": "s3"}).json() == {"storage": "s3"}
    assert client.get("/api/settings").json()["storage"] == "s3"
    assert client.put("/api/settings/storage", json={"storage": "ftp"}).status_code == 422
    assert {s["id"] for s in client.get("/api/storage").json()} == {"provider_upload", "s3", "oss"}


def test_credentials_are_never_echoed(client, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    saved = client.put("/api/credentials/dashscope", json={"values": {"api_key": SECRET}})
    assert saved.status_code == 200
    assert SECRET not in saved.text
    # "Refresh the page": a fresh GET still only shows the last 4 characters.
    for response in (client.get("/api/credentials/dashscope"), client.get("/api/credentials")):
        assert SECRET not in response.text
        assert "****qrst" in response.text


def test_credentials_list_covers_providers_and_storage(client):
    owners = {c["owner"] for c in client.get("/api/credentials").json()}
    assert {"dashscope", "gemini", "kling", "storage:s3", "storage:oss"} <= owners
    assert "mock" not in owners and "storage:provider_upload" not in owners


def test_credential_errors(client):
    assert client.get("/api/credentials/nope").status_code == 404
    assert client.put("/api/credentials/nope", json={"values": {}}).status_code == 404
    bad_field = client.put("/api/credentials/gemini", json={"values": {"token": "x"}})
    assert bad_field.status_code == 422
    too_long = client.put("/api/credentials/gemini", json={"values": {"api_key": "x" * 5000}})
    assert too_long.status_code == 422
    assert client.delete("/api/credentials/nope").status_code == 404


def test_delete_credentials(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client.put("/api/credentials/gemini", json={"values": {"api_key": SECRET}})
    assert client.delete("/api/credentials/gemini").status_code == 204
    field = client.get("/api/credentials/gemini").json()["fields"][0]
    assert field["source"] == "missing"


def test_connection_test_endpoint(client, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    assert client.post("/api/providers/mock/test").json()["status"] == "ok"
    assert client.post("/api/providers/volcengine/test").json()["status"] == "unsupported"
    assert client.post("/api/providers/nope/test").status_code == 404
    missing = client.post("/api/providers/gemini/test").json()
    assert missing["status"] == "failed" and missing["error_type"] == "auth_error"


def test_connection_test_uses_saved_key(client, monkeypatch):
    import app.api.providers as providers_api

    seen = {}

    async def fake_check(spec, credentials, client=None):
        seen.update(credentials)
        return providers_api.ConnectionResult(provider=spec.id, status="ok")

    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.setattr(providers_api, "check_provider", fake_check)
    client.put("/api/credentials/elevenlabs", json={"values": {"api_key": SECRET}})
    assert client.post("/api/providers/elevenlabs/test").json()["status"] == "ok"
    assert seen["api_key"] == SECRET


def test_undecryptable_credentials_return_conflict(client, monkeypatch):
    from app.core.config import get_settings
    from app.core.crypto import KEY_FILE_NAME, get_cipher

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    client.put("/api/credentials/gemini", json={"values": {"api_key": SECRET}})
    (get_settings().data_dir / KEY_FILE_NAME).unlink()
    get_cipher.cache_clear()
    assert client.get("/api/credentials/gemini").status_code == 409
    assert client.post("/api/providers/gemini/test").status_code == 409
    assert (
        client.put("/api/credentials/gemini", json={"values": {"api_key": "x"}}).status_code == 409
    )
