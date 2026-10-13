"""Credentials for providers and storage backends. Responses never contain full secrets."""

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field

from app.core.crypto import DecryptionError
from app.core.db import SessionDep
from app.providers.credentials import (
    CredentialView,
    UnknownCredentialOwner,
    delete_credentials,
    describe_credentials,
    save_credentials,
    storage_owner,
)
from app.providers.registry import get_registry

router = APIRouter(prefix="/credentials", tags=["credentials"])

MAX_VALUE_LENGTH = 4096


class CredentialUpdate(BaseModel):
    # Empty values keep what is stored, so the UI never needs to echo a key back.
    values: dict[str, str] = Field(default_factory=dict)


def _describe(session: SessionDep, owner: str) -> CredentialView:
    try:
        return describe_credentials(session, owner)
    except UnknownCredentialOwner as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Unknown provider or storage backend"
        ) from exc
    except DecryptionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("")
def list_credentials(session: SessionDep) -> list[CredentialView]:
    registry = get_registry()
    owners = [
        *(p.id for p in registry.providers.values() if p.credentials),
        *(storage_owner(s.id) for s in registry.storage.values() if s.credentials),
    ]
    return [_describe(session, owner) for owner in owners]


@router.get("/{owner}")
def get_credentials(owner: str, session: SessionDep) -> CredentialView:
    return _describe(session, owner)


@router.put("/{owner}")
def update_credentials(owner: str, body: CredentialUpdate, session: SessionDep) -> CredentialView:
    if any(len(v) > MAX_VALUE_LENGTH for v in body.values.values()):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Credential value is too long")
    try:
        save_credentials(session, owner, body.values)
    except UnknownCredentialOwner as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Unknown provider or storage backend"
        ) from exc
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    except DecryptionError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return _describe(session, owner)


@router.delete("/{owner}", status_code=status.HTTP_204_NO_CONTENT)
def remove_credentials(owner: str, session: SessionDep) -> Response:
    try:
        delete_credentials(session, owner)
    except UnknownCredentialOwner as exc:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "Unknown provider or storage backend"
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
