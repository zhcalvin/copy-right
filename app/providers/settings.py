"""Which provider and model serves each capability, and where uploads go."""

from pydantic import BaseModel, field_validator
from sqlmodel import Session, select

from app.capabilities.base import OPTIONAL_CAPABILITIES, Capability
from app.models import AppSetting, CapabilitySetting, utcnow
from app.providers.registry import Registry, get_registry

STORAGE_KEY = "storage"
DEFAULT_STORAGE = "provider_upload"


class CapabilitySelection(BaseModel):
    capability: Capability
    provider: str | None = None
    model: str | None = None
    enabled: bool = True

    @field_validator("model")
    @classmethod
    def _blank_model_is_default(cls, value: str | None) -> str | None:
        stripped = value.strip() if value else ""
        return stripped or None


class SettingsInvalid(ValueError):
    pass


def _selection_error(selection: CapabilitySelection, registry: Registry) -> str | None:
    capability = selection.capability
    if not selection.enabled or selection.provider is None:
        if capability in OPTIONAL_CAPABILITIES:
            return None
        return f"{capability} is required; choose a provider"
    provider = registry.providers.get(selection.provider)
    if provider is None:
        return f"{capability}: unknown provider {selection.provider}"
    if capability not in provider.capabilities:
        return f"{capability}: {provider.name} does not support it"
    options = {m.id for m in provider.models.get(capability, [])}
    if options and selection.model is not None and selection.model not in options:
        return f"{capability}: {provider.name} has no model {selection.model}"
    return None


def validate_selections(
    selections: list[CapabilitySelection], registry: Registry | None = None
) -> list[CapabilitySelection]:
    """Require exactly one valid selection per capability. Raises :class:`SettingsInvalid`."""
    reg = registry or get_registry()
    seen = [s.capability for s in selections]
    duplicates = {c for c in seen if seen.count(c) > 1}
    missing = set(Capability) - set(seen)
    errors = [
        *(f"{c} is duplicated" for c in sorted(duplicates)),
        *(f"{c} is missing" for c in sorted(missing)),
        *(e for s in selections if (e := _selection_error(s, reg))),
    ]
    if errors:
        raise SettingsInvalid("; ".join(errors))
    # Normalise switched-off capabilities so "disabled" has one representation.
    return [
        s
        if s.enabled and s.provider
        else s.model_copy(update={"enabled": False, "provider": None, "model": None})
        for s in selections
    ]


def get_capability_settings(session: Session) -> list[CapabilitySelection]:
    rows = {row.capability: row for row in session.exec(select(CapabilitySetting))}
    return [
        CapabilitySelection(
            capability=row.capability, provider=row.provider, model=row.model, enabled=row.enabled
        )
        for c in Capability
        if (row := rows.get(c)) is not None
    ]


def save_capability_settings(
    session: Session, selections: list[CapabilitySelection]
) -> list[CapabilitySelection]:
    valid = validate_selections(selections)
    for selection in valid:
        row = session.get(CapabilitySetting, selection.capability) or CapabilitySetting(
            capability=selection.capability
        )
        row.provider = selection.provider
        row.model = selection.model
        row.enabled = selection.enabled
        row.updated_at = utcnow()
        session.add(row)
    session.commit()
    return valid


def is_configured(session: Session) -> bool:
    return len(get_capability_settings(session)) == len(Capability)


def get_storage(session: Session) -> str:
    row = session.get(AppSetting, STORAGE_KEY)
    return row.value.get("id", DEFAULT_STORAGE) if row else DEFAULT_STORAGE


def set_storage(session: Session, storage_id: str) -> str:
    get_registry().storage_backend(storage_id)  # KeyError for unknown ids
    row = session.get(AppSetting, STORAGE_KEY) or AppSetting(key=STORAGE_KEY, value={})
    row.value = {"id": storage_id}
    row.updated_at = utcnow()
    session.add(row)
    session.commit()
    return storage_id
