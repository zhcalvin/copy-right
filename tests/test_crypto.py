import stat

import pytest
from keyring.errors import NoKeyringError

from app.core import crypto
from app.core.config import get_settings
from app.core.crypto import DecryptionError, get_cipher


def _key_file():
    return get_settings().data_dir / crypto.KEY_FILE_NAME


def test_file_fallback_round_trip_and_permissions():
    cipher = get_cipher()
    assert cipher.storage == "file"
    assert cipher.decrypt(cipher.encrypt(b"secret")) == b"secret"
    assert stat.S_IMODE(_key_file().stat().st_mode) == 0o600


def test_key_file_is_reused():
    first = get_cipher()
    token = first.encrypt(b"x")
    get_cipher.cache_clear()
    assert get_cipher().decrypt(token) == b"x"


def test_wrong_key_raises_decryption_error():
    token = get_cipher().encrypt(b"x")
    _key_file().unlink()
    get_cipher.cache_clear()
    with pytest.raises(DecryptionError):
        get_cipher().decrypt(token)


class FakeKeyring:
    def __init__(self, fail: bool = False):
        self.store: dict[tuple[str, str], str] = {}
        self.fail = fail

    def get_password(self, service, user):
        if self.fail:
            raise NoKeyringError("no backend")
        return self.store.get((service, user))

    def set_password(self, service, user, value):
        if self.fail:
            raise NoKeyringError("no backend")
        self.store = {**self.store, (service, user): value}


@pytest.fixture
def keyring_on(monkeypatch):
    monkeypatch.setenv("COPYRIGHT_KEYRING", "true")
    get_settings.cache_clear()
    get_cipher.cache_clear()


def test_keyring_is_preferred(monkeypatch, keyring_on):
    fake = FakeKeyring()
    monkeypatch.setattr(crypto, "keyring", fake)
    cipher = get_cipher()
    assert cipher.storage == "keyring"
    assert not _key_file().exists()
    assert (crypto.KEYRING_SERVICE, crypto.KEYRING_USERNAME) in fake.store


def test_unavailable_keyring_falls_back_to_file(monkeypatch, keyring_on):
    monkeypatch.setattr(crypto, "keyring", FakeKeyring(fail=True))
    assert get_cipher().storage == "file"
    assert _key_file().exists()


def test_existing_key_file_wins_over_empty_keyring(monkeypatch, keyring_on):
    monkeypatch.setattr(crypto, "keyring", FakeKeyring(fail=True))
    token = get_cipher().encrypt(b"kept")
    get_cipher.cache_clear()
    monkeypatch.setattr(crypto, "keyring", FakeKeyring())  # keychain now works but is empty
    cipher = get_cipher()
    assert cipher.storage == "file"
    assert cipher.decrypt(token) == b"kept"
