"""Mock adapter for every capability. Returns fixed samples so the app runs with no keys."""

import json
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
from app.capabilities.llm_text import text_artifact
from app.providers.mock import media
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
                request = self._requests.get(handle.external_id, {})
                return text_artifact(_mock_text(request), **meta)
            case Capability.IMAGE_EDIT:
                uri = media.image(handle.external_id, self._requests.get(handle.external_id, {}))
                return Artifact(
                    kind="image", uri=uri or "mock://image.png", mime_type="image/png", meta=meta
                )
            case Capability.VIDEO_EDIT_V2V | Capability.IMAGE_TO_VIDEO | Capability.LIPSYNC:
                uri = media.video(handle.external_id, self._requests.get(handle.external_id, {}))
                return Artifact(
                    kind="video", uri=uri or "mock://clip.mp4", mime_type="video/mp4", meta=meta
                )
            case Capability.TTS:
                uri = media.speech(handle.external_id, self._requests.get(handle.external_id, {}))
                return Artifact(
                    kind="audio", uri=uri or "mock://speech.wav", mime_type="audio/wav", meta=meta
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


def _mock_text(request: dict[str, Any]) -> str:
    """A canned plot edit touching the last two shots, so the review flow can be tried."""
    if request.get("task") != "plot_edit":
        return json.dumps({"summary": "Mock reply"})
    shots = [s for s in request.get("context", {}).get("shots", []) if s.get("status") != "removed"]
    if not shots:
        return json.dumps({"summary": "Nothing to change", "changes": []})
    instruction = request.get("context", {}).get("instruction", "")
    last = shots[-1]
    changes: list[dict[str, Any]] = [
        {
            "action": "change",
            "shot_id": last["shot_id"],
            "description": f"{last.get('description', '')} (Mock edit: {instruction})".strip(),
            "treatment": "regenerate",
            "reason": "Mock: the action changes completely",
        },
        {
            "action": "new",
            "after": last["shot_id"],
            "description": f"Mock new shot: {instruction}",
            "duration": 3,
            "reason": "Mock: a new ending",
        },
    ]
    if len(shots) > 1:
        changes.insert(
            0, {"action": "remove", "shot_id": shots[-2]["shot_id"], "reason": "Mock: cut"}
        )
    return json.dumps({"summary": f"Mock plot edit: {instruction}", "changes": changes})
