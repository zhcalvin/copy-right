"""Master key and Fernet encryption for stored credentials (docs/PLAN.md §5).

The master key lives in the system keychain. When the keychain is unavailable it falls
back to ``<data_dir>/master.key`` and the settings page warns about it.
"""

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import keyring
from cryptography.fernet import Fernet, InvalidToken
from keyring.errors import KeyringError

from app.core.config import get_settings

logger = logging.getLogger(__name__)

KEYRING_SERVICE = "copy-right"
KEYRING_USERNAME = "master-key"
KEY_FILE_NAME = "master.key"

KeyStorage = Literal["keyring", "file"]

# Backends raise KeyringError subclasses, but some (e.g. D-Bus) leak OS/runtime errors.
KEYCHAIN_ERRORS = (KeyringError, OSError, RuntimeError)


class DecryptionError(Exception):
    """Stored data cannot be decrypted, usually because the master key changed."""


@dataclass(frozen=True)
class Cipher:
    fernet: Fernet
    storage: KeyStorage

    def encrypt(self, plaintext: bytes) -> bytes:
        return self.fernet.encrypt(plaintext)

    def decrypt(self, token: bytes) -> bytes:
        try:
            return self.fernet.decrypt(token)
        except InvalidToken as exc:
            raise DecryptionError(
                "Saved credentials cannot be decrypted; the master key may have changed"
            ) from exc


def _keyring_get() -> str | None:
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except KEYCHAIN_ERRORS as exc:
        logger.warning("keychain unavailable for reading: %s", type(exc).__name__)
        return None


def _keyring_set(key: str) -> bool:
    try:
        keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)
    except KEYCHAIN_ERRORS as exc:
        logger.warning("keychain unavailable for writing: %s", type(exc).__name__)
        return False
    return True


def _write_key_file(path: Path, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(key)


def _load_or_create_key(key_file: Path, use_keyring: bool) -> tuple[str, KeyStorage]:
    if use_keyring:
        stored = _keyring_get()
        if stored:
            return stored, "keyring"
    # A key file from an earlier fallback must keep working, or saved data is lost.
    if key_file.exists():
        return key_file.read_text(encoding="ascii").strip(), "file"
    key = Fernet.generate_key().decode("ascii")
    if use_keyring and _keyring_set(key):
        return key, "keyring"
    _write_key_file(key_file, key)
    return key, "file"


@lru_cache
def get_cipher() -> Cipher:
    settings = get_settings()
    key, storage = _load_or_create_key(
        settings.data_dir / KEY_FILE_NAME, use_keyring=settings.keyring_enabled
    )
    return Cipher(fernet=Fernet(key.encode("ascii")), storage=storage)
