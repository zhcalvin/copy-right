"""Provider and storage credentials: encrypted at rest, masked on the way out.

Lookup order per field (docs/PLAN.md §5): environment variable → credential table →
the field's registry default. Secrets never leave this module unmasked except through
:func:`resolve_credentials`, which adapters use.
"""

import json
from collections.abc import Mapping
from os import environ
from typing import Literal

from pydantic import BaseModel
from sqlmodel import Session

from app.capabilities.base import CredentialField
from app.core.crypto import get_cipher
from app.core.secrets import mask_secret
from app.models import Credential, utcnow
from app.providers.registry import Registry, get_registry

STORAGE_PREFIX = "storage:"

FieldSource = Literal["env", "stored", "default", "missing"]


class UnknownCredentialOwner(KeyError):
    pass


class FieldView(BaseModel):
    name: str
    label: str
    secret: bool
    required: bool
    source: FieldSource
    # Masked for secrets; plain for non-secret fields such as a base URL.
    display: str


class CredentialView(BaseModel):
    owner: str
    fields: list[FieldView]
    complete: bool


def storage_owner(storage_id: str) -> str:
    return f"{STORAGE_PREFIX}{storage_id}"


def credential_fields(owner: str, registry: Registry | None = None) -> list[CredentialField]:
    """Fields for a provider id, or for ``storage:<id>``."""
    reg = registry or get_registry()
    try:
        if owner.startswith(STORAGE_PREFIX):
            return reg.storage_backend(owner.removeprefix(STORAGE_PREFIX)).credentials
        return reg.provider(owner).credentials
    except KeyError:
        raise UnknownCredentialOwner(owner) from None


def load_stored(session: Session, owner: str) -> dict[str, str]:
    row = session.get(Credential, owner)
    if row is None:
        return {}
    return json.loads(get_cipher().decrypt(row.encrypted))


def _last4(fields: list[CredentialField], values: Mapping[str, str]) -> str:
    for field in fields:
        if field.secret and values.get(field.name):
            return values[field.name][-4:]
    return ""


def save_credentials(session: Session, owner: str, updates: Mapping[str, str]) -> None:
    """Merge non-empty ``updates`` into what is stored. Empty values keep the old one.

    Raises ``ValueError`` for field names the registry does not declare.
    """
    fields = credential_fields(owner)
    known = {f.name for f in fields}
    unknown = set(updates) - known
    if unknown:
        raise ValueError(f"Unknown credential fields: {', '.join(sorted(unknown))}")

    cleaned = {name: value.strip() for name, value in updates.items() if value and value.strip()}
    merged = {**load_stored(session, owner), **cleaned}
    if not merged:
        return
    encrypted = get_cipher().encrypt(json.dumps(merged).encode("utf-8"))
    row = session.get(Credential, owner) or Credential(provider=owner, encrypted=b"", last4="")
    row.encrypted = encrypted
    row.last4 = _last4(fields, merged)
    row.updated_at = utcnow()
    session.add(row)
    session.commit()


def delete_credentials(session: Session, owner: str) -> bool:
    credential_fields(owner)  # validate the owner
    row = session.get(Credential, owner)
    if row is None:
        return False
    session.delete(row)
    session.commit()
    return True


def _resolve_field(
    field: CredentialField, stored: Mapping[str, str], env: Mapping[str, str]
) -> tuple[str | None, FieldSource]:
    if field.env and env.get(field.env):
        return env[field.env], "env"
    if stored.get(field.name):
        return stored[field.name], "stored"
    if field.default:
        return field.default, "default"
    return None, "missing"


def resolve_credentials(
    fields: list[CredentialField],
    stored: Mapping[str, str] | None = None,
    env: Mapping[str, str] = environ,
) -> dict[str, str]:
    """Plain values for an adapter. Missing fields are left out."""
    pairs = ((f.name, _resolve_field(f, stored or {}, env)[0]) for f in fields)
    return {name: value for name, value in pairs if value is not None}


def credentials_for(
    session: Session, owner: str, env: Mapping[str, str] = environ
) -> dict[str, str]:
    return resolve_credentials(credential_fields(owner), load_stored(session, owner), env)


def _field_view(field: CredentialField, value: str | None, source: FieldSource) -> FieldView:
    return FieldView(
        name=field.name,
        label=field.label,
        secret=field.secret,
        required=field.required,
        source=source,
        display=mask_secret(value) if field.secret else (value or ""),
    )


def describe_credentials(
    session: Session, owner: str, env: Mapping[str, str] = environ
) -> CredentialView:
    fields = credential_fields(owner)
    stored = load_stored(session, owner)
    views = [_field_view(field, *_resolve_field(field, stored, env)) for field in fields]
    complete = all(v.source != "missing" for v in views if v.required)
    return CredentialView(owner=owner, fields=views, complete=complete)
