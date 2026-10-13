"""Settings → Models & keys: capability selections and the storage backend."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from app.core.crypto import KeyStorage, get_cipher
from app.core.db import SessionDep
from app.providers.settings import (
    CapabilitySelection,
    SettingsInvalid,
    get_capability_settings,
    get_storage,
    is_configured,
    save_capability_settings,
    set_storage,
)

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsRead(BaseModel):
    configured: bool
    capabilities: list[CapabilitySelection]
    storage: str
    # "file" means the keychain was unavailable; the settings page warns about it.
    key_storage: KeyStorage


class StorageUpdate(BaseModel):
    storage: str


@router.get("")
def read_settings(session: SessionDep) -> SettingsRead:
    return SettingsRead(
        configured=is_configured(session),
        capabilities=get_capability_settings(session),
        storage=get_storage(session),
        key_storage=get_cipher().storage,
    )


@router.put("/capabilities")
def update_capabilities(
    body: list[CapabilitySelection], session: SessionDep
) -> list[CapabilitySelection]:
    try:
        return save_capability_settings(session, body)
    except SettingsInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


@router.put("/storage")
def update_storage(body: StorageUpdate, session: SessionDep) -> StorageUpdate:
    try:
        return StorageUpdate(storage=set_storage(session, body.storage))
    except KeyError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Unknown storage backend"
        ) from exc
