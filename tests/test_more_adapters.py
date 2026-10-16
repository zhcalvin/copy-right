"""Runway, Kling, ElevenLabs, Sync Labs and Gemini generation, against mocked HTTP."""

import base64
import json
from pathlib import Path

import httpx
import pytest

from app.capabilities.base import Capability, ContentRejected, InputInvalid, RemoteStatus
from app.providers.elevenlabs.adapter import ElevenLabsAdapter
from app.providers.gemini.adapter import GeminiAdapter
from app.providers.kling.adapter import KlingAdapter
from app.providers.runway.adapter import RunwayAdapter
from app.providers.synclabs.adapter import SyncLabsAdapter


class Routes:
    def __init__(self, routes: dict[tuple[str, str], httpx.Response]):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.routes[(request.method, request.url.path)]

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self))

    def body(self, path: str) -> dict:
        return json.loads([r for r in self.requests if r.url.path == path][-1].content)


@pytest.fixture
def files(tmp_path) -> dict[str, Path]:
    paths = {name: tmp_path / name for name in ("frame.png", "clip.mp4", "ref.png", "voice.mp3")}
    for path in paths.values():
        path.write_bytes(b"fake " + path.name.encode())
    return {k.split(".")[0]: v for k, v in paths.items()}


async def test_runway_edit_with_references_and_i2v(files):
    routes = Routes(
        {
            ("POST", "/v1/video_to_video"): httpx.Response(200, json={"id": "t1"}),
            ("POST", "/v1/image_to_video"): httpx.Response(200, json={"id": "t2"}),
            ("GET", "/v1/tasks/t1"): httpx.Response(
                200,
                json={
                    "status": "SUCCEEDED",
                    "output": ["https://cdn/o.mp4"],
                    "cost": {"credits": 50},
                },
            ),
            ("GET", "/v1/tasks/t2"): httpx.Response(
                200, json={"status": "FAILED", "failureCode": "SAFETY.INPUT", "failure": "no"}
            ),
        }
    )
    creds = {"api_key": "rw-secret", "base_url": "https://rw.example"}
    editor = RunwayAdapter(credentials=creds, model="gemini_omni_flash_1.1", client=routes.client())
    handle = await editor.submit(
        Capability.VIDEO_EDIT_V2V,
        {
            "video": str(files["clip"]),
            "reference_images": [str(files["ref"])],
            "mode": "edit",
            "prompt": "p",
        },
    )
    body = routes.body("/v1/video_to_video")
    assert body["videoUri"].startswith("data:video/mp4;base64,") and len(body["references"]) == 1
    assert routes.requests[0].headers["x-runway-version"] == "2024-11-06"
    assert (await editor.poll(handle)).status == RemoteStatus.SUCCEEDED
    assert (await editor.fetch_result(handle)).uri == "https://cdn/o.mp4"

    aleph = RunwayAdapter(credentials=creds, model="aleph2", client=routes.client())
    await aleph.submit(
        Capability.VIDEO_EDIT_V2V,
        {
            "video": str(files["clip"]),
            "reference_images": [str(files["ref"])],
            "mode": "edit",
            "prompt": "p",
        },
    )
    assert "references" not in routes.body("/v1/video_to_video")  # aleph2 takes none
    with pytest.raises(InputInvalid, match="does not do swap"):
        await aleph.submit(
            Capability.VIDEO_EDIT_V2V,
            {"video": str(files["clip"]), "reference_images": [str(files["ref"])]},
        )

    animator = RunwayAdapter(credentials=creds, model="gen4.5", client=routes.client())
    i2v = await animator.submit(
        Capability.IMAGE_TO_VIDEO,
        {"first_frame": str(files["frame"]), "prompt": "go", "duration": 0.7},
    )
    assert routes.body("/v1/image_to_video")["duration"] == 2
    failed = await animator.poll(i2v)
    assert failed.status == RemoteStatus.FAILED and failed.error_type == "content_rejected"


async def test_kling_image_to_video(files):
    path = "/v1/videos/image2video"
    routes = Routes(
        {
            ("POST", path): httpx.Response(200, json={"code": 0, "data": {"task_id": "k1"}}),
            ("GET", f"{path}/k1"): httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "task_status": "succeed",
                        "task_result": {"videos": [{"url": "https://k/v.mp4"}]},
                    },
                },
            ),
        }
    )
    adapter = KlingAdapter(
        credentials={"api_key": "k", "base_url": "https://kling.example"},
        model="kling-v2-1",
        client=routes.client(),
    )
    handle = await adapter.submit(
        Capability.IMAGE_TO_VIDEO,
        {"first_frame": str(files["frame"]), "prompt": "go", "duration": 7, "resolution": "1080P"},
    )
    body = routes.body(path)
    assert body["duration"] == "10" and body["mode"] == "pro"
    assert base64.b64decode(body["image"]) == b"fake frame.png"
    assert (await adapter.poll(handle)).status == RemoteStatus.SUCCEEDED
    assert (await adapter.fetch_result(handle)).uri == "https://k/v.mp4"

    risky = Routes(
        {("POST", path): httpx.Response(200, json={"code": 1301, "message": "risk control"})}
    )
    blocked = KlingAdapter(
        credentials={"api_key": "k", "base_url": "https://kling.example"},
        model="m",
        client=risky.client(),
    )
    with pytest.raises(ContentRejected):
        await blocked.submit(
            Capability.IMAGE_TO_VIDEO, {"first_frame": str(files["frame"]), "prompt": "x"}
        )


async def test_elevenlabs_saves_audio_locally():
    routes = Routes(
        {("POST", "/v1/text-to-speech/voice-1"): httpx.Response(200, content=b"ID3 mp3")}
    )
    adapter = ElevenLabsAdapter(
        credentials={"api_key": "e", "base_url": "https://el.example"},
        model="eleven_multilingual_v2",
        client=routes.client(),
    )
    handle = await adapter.submit(Capability.TTS, {"text": "Hello", "voice": "voice-1"})
    artifact = await adapter.fetch_result(handle)
    assert Path(artifact.uri).read_bytes() == b"ID3 mp3"
    assert routes.requests[0].url.params["output_format"] == "mp3_44100_128"
    assert routes.body("/v1/text-to-speech/voice-1")["model_id"] == "eleven_multilingual_v2"


async def test_synclabs_uploads_files_and_polls(files):
    routes = Routes(
        {
            ("POST", "/v2/generate"): httpx.Response(201, json={"id": "g1", "status": "PENDING"}),
            ("GET", "/v2/generate/g1"): httpx.Response(
                200, json={"status": "COMPLETED", "outputUrl": "https://s/o.mp4"}
            ),
        }
    )
    adapter = SyncLabsAdapter(
        credentials={"api_key": "s", "base_url": "https://sync.example"},
        model="lipsync-2",
        client=routes.client(),
    )
    handle = await adapter.submit(
        Capability.LIPSYNC, {"video": str(files["clip"]), "audio": str(files["voice"])}
    )
    sent = routes.requests[0]
    assert (
        sent.headers["x-api-key"] == "s"
        and b'name="video"' in sent.content
        and b"lipsync-2" in sent.content
    )
    assert (await adapter.poll(handle)).status == RemoteStatus.SUCCEEDED
    assert (await adapter.fetch_result(handle)).uri == "https://s/o.mp4"


def _inline_reply(mime: str, data: bytes) -> httpx.Response:
    part = {"inlineData": {"mimeType": mime, "data": base64.b64encode(data).decode()}}
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [part]}}]})


async def test_gemini_image_speech_and_veo(files):
    creds = {"api_key": "g", "base_url": "https://gm.example/v1beta"}
    routes = Routes(
        {
            ("POST", "/v1beta/models/img:generateContent"): _inline_reply("image/png", b"PNG"),
            ("POST", "/v1beta/models/tts:generateContent"): _inline_reply(
                "audio/L16", b"\x00\x01" * 10
            ),
            ("POST", "/v1beta/models/veo:predictLongRunning"): httpx.Response(
                200, json={"name": "operations/op1"}
            ),
            ("GET", "/v1beta/operations/op1"): httpx.Response(
                200,
                json={
                    "done": True,
                    "response": {
                        "generateVideoResponse": {
                            "generatedSamples": [
                                {"video": {"uri": "https://gm.example/v1beta/files/v:download"}}
                            ]
                        }
                    },
                },
            ),
            ("GET", "/v1beta/files/v:download"): httpx.Response(200, content=b"MP4"),
        }
    )
    image = GeminiAdapter(credentials=creds, model="img", client=routes.client())
    artifact = await image.fetch_result(
        await image.submit(
            Capability.IMAGE_EDIT, {"images": [str(files["ref"])], "prompt": "a sheep"}
        )
    )
    assert Path(artifact.uri).read_bytes() == b"PNG"
    assert routes.body("/v1beta/models/img:generateContent")["generationConfig"] == {
        "responseModalities": ["IMAGE"]
    }

    speech = GeminiAdapter(credentials=creds, model="tts", client=routes.client())
    wav = await speech.fetch_result(
        await speech.submit(Capability.TTS, {"text": "hi", "voice": "Puck"})
    )
    assert Path(wav.uri).read_bytes()[:4] == b"RIFF"

    veo = GeminiAdapter(credentials=creds, model="veo", client=routes.client())
    handle = await veo.submit(
        Capability.IMAGE_TO_VIDEO,
        {"first_frame": str(files["frame"]), "prompt": "go", "duration": 5},
    )
    params = routes.body("/v1beta/models/veo:predictLongRunning")["parameters"]
    assert params["durationSeconds"] == "6" and params["resolution"] == "720p"
    assert (await veo.poll(handle)).status == RemoteStatus.SUCCEEDED
    video = await veo.fetch_result(handle)
    assert Path(video.uri).read_bytes() == b"MP4"
    download = routes.requests[-1]
    assert download.headers["x-goog-api-key"] == "g"


async def test_gemini_veo_filtered_is_content_rejected(files):
    routes = Routes(
        {
            ("GET", "/v1beta/operations/op2"): httpx.Response(
                200,
                json={
                    "done": True,
                    "response": {"generateVideoResponse": {"raiMediaFilteredReasons": ["person"]}},
                },
            )
        }
    )
    from app.capabilities.base import JobHandle

    adapter = GeminiAdapter(
        credentials={"api_key": "g", "base_url": "https://gm.example/v1beta"},
        model="veo",
        client=routes.client(),
    )
    handle = JobHandle(
        provider="gemini", capability=Capability.IMAGE_TO_VIDEO, external_id="operations/op2"
    )
    result = await adapter.poll(handle)
    assert result.error_type == "content_rejected"
