import pytest

from app.capabilities.base import Capability, InputInvalid, RemoteStatus
from app.providers.mock.adapter import ASYNC_CAPABILITIES, MockAdapter
from app.storyboard.schema import StoryboardDoc


@pytest.mark.parametrize("capability", list(Capability))
async def test_every_capability_completes(capability):
    adapter = MockAdapter()
    handle = await adapter.submit(capability, {})
    status = (await adapter.poll(handle)).status
    if capability in ASYNC_CAPABILITIES:
        assert status == RemoteStatus.RUNNING
        status = (await adapter.poll(handle)).status
    assert status == RemoteStatus.SUCCEEDED
    artifact = await adapter.fetch_result(handle)
    assert artifact.meta["mock"] is True


async def test_video_understanding_returns_valid_storyboard():
    adapter = MockAdapter()
    handle = await adapter.submit(Capability.VIDEO_UNDERSTANDING, {})
    artifact = await adapter.fetch_result(handle)
    doc = StoryboardDoc.model_validate(artifact.data)
    assert len(doc.shots) == 3


async def test_external_ids_are_unique():
    adapter = MockAdapter()
    a = await adapter.submit(Capability.TTS, {})
    b = await adapter.submit(Capability.TTS, {})
    assert a.external_id != b.external_id


async def test_unsupported_capability_is_input_invalid(monkeypatch):
    monkeypatch.setattr(MockAdapter, "capabilities", frozenset({Capability.TTS}))
    with pytest.raises(InputInvalid):
        await MockAdapter().submit(Capability.ASR, {})


async def test_connection_succeeds():
    await MockAdapter().test_connection()
