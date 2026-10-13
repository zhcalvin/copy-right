import pytest
from sqlmodel import Session

from app.models import Credential
from app.providers.credentials import (
    UnknownCredentialOwner,
    credentials_for,
    delete_credentials,
    describe_credentials,
    load_stored,
    save_credentials,
    storage_owner,
)

SECRET = "sk-test-1234567890wxyz"


@pytest.fixture
def session(engine):
    with Session(engine) as s:
        yield s


def test_saved_secret_is_encrypted_at_rest(session):
    save_credentials(session, "dashscope", {"api_key": SECRET})
    row = session.get(Credential, "dashscope")
    assert SECRET.encode() not in row.encrypted
    assert row.last4 == "wxyz"
    assert load_stored(session, "dashscope") == {"api_key": SECRET}


def test_blank_update_keeps_existing_value(session):
    save_credentials(session, "dashscope", {"api_key": SECRET})
    save_credentials(session, "dashscope", {"api_key": "  ", "base_url": "https://ws.example"})
    assert load_stored(session, "dashscope") == {
        "api_key": SECRET,
        "base_url": "https://ws.example",
    }


def test_unknown_field_and_owner_are_rejected(session):
    with pytest.raises(ValueError, match="Unknown credential fields"):
        save_credentials(session, "dashscope", {"password": "x"})
    with pytest.raises(UnknownCredentialOwner):
        save_credentials(session, "nope", {"api_key": "x"})
    with pytest.raises(UnknownCredentialOwner):
        describe_credentials(session, "storage:nope")


def test_view_masks_secrets_and_reports_sources(session):
    save_credentials(session, "dashscope", {"api_key": SECRET})
    view = describe_credentials(session, "dashscope", env={})
    fields = {f.name: f for f in view.fields}
    assert fields["api_key"].display == "****wxyz"
    assert fields["api_key"].source == "stored"
    assert fields["base_url"].source == "default"
    assert fields["base_url"].display.startswith("https://")
    assert view.complete is True
    assert SECRET not in view.model_dump_json()


def test_env_takes_priority_over_stored(session):
    save_credentials(session, "gemini", {"api_key": SECRET})
    env = {"GEMINI_API_KEY": "env-key-0000"}
    assert credentials_for(session, "gemini", env=env) == {"api_key": "env-key-0000"}
    view = describe_credentials(session, "gemini", env=env)
    assert view.fields[0].source == "env"
    assert view.fields[0].display == "****0000"


def test_incomplete_when_required_field_missing(session):
    view = describe_credentials(session, storage_owner("s3"), env={})
    assert view.complete is False
    save_credentials(
        session,
        storage_owner("s3"),
        {
            "endpoint": "https://r2.example",
            "bucket": "b",
            "access_key_id": "a",
            "secret_access_key": "s",
        },
    )
    assert describe_credentials(session, storage_owner("s3"), env={}).complete is True


def test_delete(session):
    save_credentials(session, "gemini", {"api_key": SECRET})
    assert delete_credentials(session, "gemini") is True
    assert delete_credentials(session, "gemini") is False
    assert load_stored(session, "gemini") == {}


def test_empty_save_writes_nothing(session):
    save_credentials(session, "gemini", {"api_key": ""})
    assert session.get(Credential, "gemini") is None
