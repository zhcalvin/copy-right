"""Read-only view of the provider registry, used by the setup wizard."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.capabilities.base import OPTIONAL_CAPABILITIES, Capability
from app.core.crypto import DecryptionError
from app.core.db import SessionDep
from app.providers.connection import ConnectionResult, check_provider
from app.providers.credentials import credentials_for
from app.providers.registry import PresetSpec, ProviderSpec, StorageSpec, get_registry

router = APIRouter(tags=["providers"])


class CapabilityRead(BaseModel):
    id: Capability
    optional: bool


@router.get("/capabilities")
def list_capabilities() -> list[CapabilityRead]:
    return [CapabilityRead(id=c, optional=c in OPTIONAL_CAPABILITIES) for c in Capability]


@router.get("/providers")
def list_providers() -> list[ProviderSpec]:
    return list(get_registry().providers.values())


@router.get("/presets")
def list_presets() -> list[PresetSpec]:
    return list(get_registry().presets.values())


@router.get("/storage")
def list_storage() -> list[StorageSpec]:
    return list(get_registry().storage.values())


@router.post("/providers/{provider_id}/test")
async def test_provider_connection(provider_id: str, session: SessionDep) -> ConnectionResult:
    """Uses saved (or environment) credentials. Never triggers billable generation."""
    try:
        spec = get_registry().provider(provider_id)
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown provider") from exc
    try:
        credentials = credentials_for(session, provider_id)
    except DecryptionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await check_provider(spec, credentials)
