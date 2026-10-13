import pytest
import yaml
from pydantic import ValidationError

from app.capabilities.base import Capability
from app.providers.credentials import resolve_credentials
from app.providers.mock.adapter import MockAdapter
from app.providers.registry import (
    REGISTRY_PATH,
    ProviderNotImplemented,
    build_adapter,
    load_registry,
)


def test_registry_loads_with_all_presets():
    registry = load_registry()
    assert {"cn", "global", "demo"} <= set(registry.presets)


def test_every_preset_covers_all_capabilities():
    for preset in load_registry().presets.values():
        assert set(preset.capabilities) == set(Capability)


def test_demo_preset_is_all_mock():
    demo = load_registry().presets["demo"]
    assert set(demo.capabilities.values()) == {"mock"}


def test_preset_regions_match():
    registry = load_registry()
    for preset_id, region in (("cn", "cn"), ("global", "global")):
        for provider_id in registry.presets[preset_id].capabilities.values():
            assert registry.providers[provider_id].region == region


def test_mock_is_the_only_implemented_provider_for_now():
    implemented = {p.id for p in load_registry().providers.values() if p.implemented}
    assert implemented == {"mock"}


def test_build_mock_adapter():
    adapter = build_adapter("mock", model="mock-vu")
    assert isinstance(adapter, MockAdapter)
    assert adapter.model == "mock-vu"


def test_build_unimplemented_provider_raises():
    with pytest.raises(ProviderNotImplemented):
        build_adapter("dashscope")


def test_build_unknown_provider_raises():
    with pytest.raises(KeyError):
        build_adapter("nope")


def test_preset_referencing_unsupported_capability_is_rejected(tmp_path):
    raw = yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))
    raw["presets"]["global"]["capabilities"]["tts"] = "runway"  # runway has no tts
    bad = tmp_path / "registry.yaml"
    bad.write_text(yaml.safe_dump(raw, allow_unicode=True), encoding="utf-8")
    with pytest.raises(ValidationError, match="does not support"):
        load_registry(bad)


def test_credentials_come_from_env_vars_then_defaults():
    fields = load_registry().provider("kling").credentials
    env = {"KLING_API_KEY": "kk", "OTHER": "x"}
    default_url = "https://api-beijing.klingai.com"
    assert resolve_credentials(fields, env=env) == {"api_key": "kk", "base_url": default_url}
    assert resolve_credentials(fields, env={}) == {"base_url": default_url}


def test_display_text_is_english_with_chinese_overrides():
    registry = load_registry()
    entries = [*registry.providers.values(), *registry.presets.values(), *registry.storage.values()]
    for entry in entries:
        assert not any("一" <= ch <= "鿿" for ch in entry.name + entry.description), entry.id
        assert set(entry.i18n) == {"zh-CN"}, entry.id
        assert set(entry.i18n["zh-CN"]) <= {"name", "description"}, entry.id
