"""Load ``registry.yaml`` and construct adapters from it."""

from functools import lru_cache
from importlib import import_module
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, computed_field, model_validator

from app.capabilities.base import Adapter, Capability, CredentialField

REGISTRY_PATH = Path(__file__).with_name("registry.yaml")


# Optional per-locale overrides of display text, e.g. {"zh-CN": {"name": "…"}}.
# The plain fields are English, the default UI language.
Translations = dict[str, dict[str, str]]


class ModelOption(BaseModel):
    id: str
    name: str
    # For video_edit_v2v: the request modes (treatments) this model serves.
    modes: list[str] = Field(default_factory=list)
    # Input clip length the model accepts; shorter clips are padded, longer ones split.
    min_seconds: float | None = None
    max_seconds: float | None = None


class VoiceOption(BaseModel):
    id: str
    name: str
    gender: Literal["female", "male", "other"] = "other"
    languages: list[str] = Field(default_factory=list)


StatusOutcome = Literal["ok", "auth_error", "quota_error"]


class ConnectionTestSpec(BaseModel):
    """A free, read-only HTTP request that proves the credentials work.

    ``url``, ``headers`` and ``query`` values are templates: ``{field}`` is replaced by
    the credential field of that name, and ``{now_ms}`` / ``{day_ago_ms}`` by Unix
    timestamps in milliseconds. ``status`` overrides how a response code is read.
    """

    method: Literal["GET"] = "GET"
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, str] = Field(default_factory=dict)
    status: dict[int, StatusOutcome] = Field(default_factory=dict)


class ProviderSpec(BaseModel):
    id: str
    name: str
    region: Literal["cn", "global", "any"]
    adapter: str | None = None
    description: str = ""
    i18n: Translations = Field(default_factory=dict)
    docs: str | None = None
    capabilities: list[Capability]
    credentials: list[CredentialField] = Field(default_factory=list)
    models: dict[Capability, list[ModelOption]] = Field(default_factory=dict)
    # System voices for ``tts``; the first is the default.
    voices: list[VoiceOption] = Field(default_factory=list)
    connection_test: ConnectionTestSpec | None = None

    @computed_field
    @property
    def implemented(self) -> bool:
        return self.adapter is not None

    @model_validator(mode="after")
    def _models_match_capabilities(self) -> "ProviderSpec":
        extra = set(self.models) - set(self.capabilities)
        if extra:
            raise ValueError(f"provider {self.id}: models for undeclared capabilities {extra}")
        return self


class StorageSpec(BaseModel):
    """Where inputs go when a provider only accepts public URLs (docs/PLAN.md §4)."""

    id: str
    name: str
    description: str = ""
    i18n: Translations = Field(default_factory=dict)
    docs: str | None = None
    credentials: list[CredentialField] = Field(default_factory=list)


class PresetSpec(BaseModel):
    id: str
    name: str
    description: str = ""
    i18n: Translations = Field(default_factory=dict)
    capabilities: dict[Capability, str | None]


class Registry(BaseModel):
    providers: dict[str, ProviderSpec]
    presets: dict[str, PresetSpec]
    storage: dict[str, StorageSpec] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _presets_are_consistent(self) -> "Registry":
        for preset in self.presets.values():
            missing = set(Capability) - set(preset.capabilities)
            if missing:
                raise ValueError(f"preset {preset.id}: missing capabilities {sorted(missing)}")
            for capability, provider_id in preset.capabilities.items():
                if provider_id is None:
                    continue
                provider = self.providers.get(provider_id)
                if provider is None:
                    raise ValueError(f"preset {preset.id}: unknown provider {provider_id}")
                if capability not in provider.capabilities:
                    raise ValueError(
                        f"preset {preset.id}: {provider_id} does not support {capability}"
                    )
        return self

    def provider(self, provider_id: str) -> ProviderSpec:
        try:
            return self.providers[provider_id]
        except KeyError:
            raise KeyError(f"unknown provider: {provider_id}") from None

    def storage_backend(self, storage_id: str) -> StorageSpec:
        try:
            return self.storage[storage_id]
        except KeyError:
            raise KeyError(f"unknown storage backend: {storage_id}") from None


def load_registry(path: Path = REGISTRY_PATH) -> Registry:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    providers = {pid: {"id": pid, **spec} for pid, spec in raw.get("providers", {}).items()}
    presets = {pid: {"id": pid, **spec} for pid, spec in raw.get("presets", {}).items()}
    storage = {sid: {"id": sid, **spec} for sid, spec in raw.get("storage", {}).items()}
    return Registry.model_validate({"providers": providers, "presets": presets, "storage": storage})


@lru_cache
def get_registry() -> Registry:
    return load_registry()


class ProviderNotImplemented(Exception):
    pass


def _import_adapter_class(dotted: str) -> type[Adapter]:
    module_path, _, class_name = dotted.partition(":")
    cls = getattr(import_module(module_path), class_name)
    if not (isinstance(cls, type) and issubclass(cls, Adapter)):
        raise TypeError(f"{dotted} is not an Adapter subclass")
    return cls


def build_adapter(
    provider_id: str,
    *,
    credentials: dict[str, str] | None = None,
    model: str | None = None,
    registry: Registry | None = None,
) -> Adapter:
    spec = (registry or get_registry()).provider(provider_id)
    if spec.adapter is None:
        raise ProviderNotImplemented(f"provider {provider_id} has no adapter yet")
    return _import_adapter_class(spec.adapter)(credentials=credentials, model=model)
