"""Mock adapter for every capability. Returns fixed samples so the app runs with no keys."""

from itertools import count
from typing import Any, ClassVar

from app.capabilities.base import (
    Adapter,
    Artifact,
    Capability,
    InputInvalid,
    JobHandle,
    PollResult,
    RemoteStatus,
)
from app.storyboard.samples import load_sample_storyboard

# Capabilities that are asynchronous at real providers. The mock reports RUNNING on the
# first poll so the worker's polling path gets exercised.
ASYNC_CAPABILITIES = frozenset(
    {Capability.VIDEO_EDIT_V2V, Capability.IMAGE_TO_VIDEO, Capability.LIPSYNC}
)


class MockAdapter(Adapter):
    provider_id: ClassVar[str] = "mock"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(Capability)

    _ids: ClassVar[count] = count(1)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._poll_counts: dict[str, int] = {}
        self._requests: dict[str, dict[str, Any]] = {}

    async def submit(self, capability: Capability, request: dict[str, Any]) -> JobHandle:
        if not self.supports(capability):
            raise InputInvalid(f"mock does not support {capability}", provider=self.provider_id)
        external_id = f"mock-{capability}-{next(self._ids)}"
        self._requests = {**self._requests, external_id: request}
        return JobHandle(
            provider=self.provider_id,
            capability=capability,
            external_id=external_id,
            model=self.model,
        )

    async def poll(self, handle: JobHandle) -> PollResult:
        polls = self._poll_counts.get(handle.external_id, 0) + 1
        self._poll_counts = {**self._poll_counts, handle.external_id: polls}
        if handle.capability in ASYNC_CAPABILITIES and polls == 1:
            return PollResult(status=RemoteStatus.RUNNING, progress=0.5)
        return PollResult(status=RemoteStatus.SUCCEEDED, progress=1.0)

    async def fetch_result(self, handle: JobHandle) -> Artifact:
        meta = {"mock": True, "external_id": handle.external_id}
        match handle.capability:
            case Capability.VIDEO_UNDERSTANDING:
                request = self._requests.get(handle.external_id, {})
                if request.get("shots"):  # a real parse: describe the shots it was given
                    return Artifact(kind="json", data=_describe(request["shots"]), meta=meta)
                return Artifact(kind="json", data=load_sample_storyboard(), meta=meta)
            case Capability.LLM_TEXT:
                return Artifact(
                    kind="json",
                    data={"storyboard": None, "affected_shots": [], "summary": "Mock: no changes"},
                    meta=meta,
                )
            case Capability.IMAGE_EDIT:
                return Artifact(
                    kind="image", uri="mock://image.png", mime_type="image/png", meta=meta
                )
            case Capability.VIDEO_EDIT_V2V | Capability.IMAGE_TO_VIDEO | Capability.LIPSYNC:
                return Artifact(
                    kind="video", uri="mock://clip.mp4", mime_type="video/mp4", meta=meta
                )
            case Capability.TTS:
                return Artifact(
                    kind="audio", uri="mock://speech.wav", mime_type="audio/wav", meta=meta
                )
            case Capability.ASR:
                return Artifact(
                    kind="json",
                    data={
                        "segments": [
                            {
                                "start": 4.4,
                                "end": 6.6,
                                "speaker": "char_a",
                                "text": "I'm not sure whether I should go.",
                            }
                        ]
                    },
                    meta=meta,
                )
        raise InputInvalid(f"unhandled capability {handle.capability}", provider=self.provider_id)

    async def test_connection(self) -> None:
        return None


def _describe(shots: list[dict[str, Any]]) -> dict[str, Any]:
    """A placeholder storyboard with one character and one scene throughout."""
    return {
        "characters": [{"character_id": "char_1", "name": "Person", "description": "Mock"}],
        "scenes": [{"scene_id": "scene_1", "description": "Mock location"}],
        "shots": [
            {
                "shot_id": shot["shot_id"],
                "description": f"Mock description of shot {shot['shot_id']}",
                "characters": [{"character_id": "char_1", "action": "stands", "expression": None}],
                "dialogue": [],
                "visual": {"shot_size": "medium", "camera_motion": "static"},
                "scene_id": "scene_1",
            }
            for shot in shots
        ],
    }
