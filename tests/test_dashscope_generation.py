import json

import httpx
import pytest

from app.capabilities.base import (
    Capability,
    ContentRejected,
    InputInvalid,
    ProviderError,
    RemoteStatus,
)
from app.providers.dashscope import native
from app.providers.dashscope.adapter import DashScopeAdapter
from app.tools.media import cut_clip

SECRET = "sk-test-abcdefgh1234"
CREDS = {"api_key": SECRET, "base_url": "https://ds.example/compatible-mode/v1"}
NATIVE = "https://ds.example/api/v1"
POLICY = {
    "data": {
        "upload_dir": "dashscope-instant/abc",
        "upload_host": "https://oss.example",
        "oss_access_key_id": "id",
        "signature": "sig",
        "policy": "pol",
        "x_oss_object_acl": "private",
        "x_oss_forbid_overwrite": "true",
    }
}


class Recorder:
    """A MockTransport handler that answers by (method, path) and records requests."""

    def __init__(self, routes: dict[tuple[str, str], httpx.Response]):
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.routes[(request.method, request.url.path)]

    def adapter(self, model: str = "m") -> DashScopeAdapter:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self))
        return DashScopeAdapter(credentials=CREDS, model=model, client=client)

    def sent(self, path: str) -> httpx.Request:
        return next(r for r in self.requests if r.url.path == path)


@pytest.fixture
def image(tmp_path):
    path = tmp_path / "character.png"
    path.write_bytes(b"\x89PNG fake")
    return path


def test_native_base():
    assert native.native_base("https://h/compatible-mode/v1/") == "https://h/api/v1"
    assert native.native_base("https://h/api/v1") == "https://h/api/v1"
    assert native.native_base("https://h") == "https://h/api/v1"


async def test_image_edit(image):
    reply = {"output": {"choices": [{"message": {"content": [{"image": "https://cdn/x.png"}]}}]}}
    recorder = Recorder(
        {
            ("POST", "/api/v1/services/aigc/multimodal-generation/generation"): httpx.Response(
                200, json=reply
            )
        }
    )
    adapter = recorder.adapter("qwen-image-test")
    request = {"images": [str(image)], "prompt": "make it a sheep", "size": "1024*1536"}
    handle = await adapter.submit(Capability.IMAGE_EDIT, request)
    artifact = await adapter.fetch_result(handle)
    assert artifact.kind == "image" and artifact.uri == "https://cdn/x.png"
    body = json.loads(recorder.requests[0].content)
    content = body["input"]["messages"][0]["content"]
    assert content[0]["image"].startswith("data:image/png;base64,")
    assert content[-1] == {"text": "make it a sheep"}
    assert body["parameters"]["size"] == "1024*1536" and body["model"] == "qwen-image-test"


async def test_image_edit_errors(image):
    empty = {"output": {"choices": [{"message": {"content": [{"text": "no"}]}}]}}
    path = "/api/v1/services/aigc/multimodal-generation/generation"
    adapter = Recorder({("POST", path): httpx.Response(200, json=empty)}).adapter()
    with pytest.raises(InputInvalid, match="no image"):
        await adapter.submit(Capability.IMAGE_EDIT, {"images": [str(image)], "prompt": "x"})
    broken = Recorder({("POST", path): httpx.Response(200, json={})}).adapter()
    with pytest.raises(ProviderError):
        await broken.submit(Capability.IMAGE_EDIT, {"images": [str(image)], "prompt": "x"})
    with pytest.raises(InputInvalid, match="Bad request at images"):
        await broken.submit(Capability.IMAGE_EDIT, {"images": [], "prompt": "x"})


async def test_character_swap_uploads_and_polls(image, sample_video):
    recorder = Recorder(
        {
            ("GET", "/api/v1/uploads"): httpx.Response(200, json=POLICY),
            ("POST", "/"): httpx.Response(200),
            ("POST", "/api/v1/services/aigc/image2video/video-synthesis"): httpx.Response(
                200, json={"output": {"task_id": "task-1", "task_status": "PENDING"}}
            ),
            ("GET", "/api/v1/tasks/task-1"): httpx.Response(
                200,
                json={
                    "output": {
                        "task_status": "SUCCEEDED",
                        "results": {"video_url": "https://cdn/v.mp4"},
                    },
                    "usage": {"video_duration": 3.5},
                },
            ),
        }
    )
    adapter = recorder.adapter("wan2.2-animate-mix")
    request = {"video": str(sample_video), "reference_images": [str(image)], "quality": "pro"}
    handle = await adapter.submit(Capability.VIDEO_EDIT_V2V, request)
    assert handle.external_id == "task-1"

    create = recorder.sent("/api/v1/services/aigc/image2video/video-synthesis")
    assert create.headers["x-dashscope-async"] == "enable"
    assert create.headers["x-dashscope-ossresourceresolve"] == "enable"
    body = json.loads(create.content)
    assert body["input"]["image_url"] == "oss://dashscope-instant/abc/character.png"
    assert body["input"]["video_url"] == f"oss://dashscope-instant/abc/{sample_video.name}"
    assert body["parameters"] == {"mode": "wan-pro"}
    policy_request = recorder.sent("/api/v1/uploads")
    assert policy_request.url.params["model"] == "wan2.2-animate-mix"

    assert (await adapter.poll(handle)).status == RemoteStatus.SUCCEEDED
    artifact = await adapter.fetch_result(handle)
    assert artifact.uri == "https://cdn/v.mp4" and artifact.meta["usage"] == {"video_duration": 3.5}


async def test_character_swap_rejects_clips_outside_2_to_30_seconds(image, sample_video, tmp_path):
    short = cut_clip(sample_video, 0, 1.0, tmp_path / "short.mp4")
    adapter = Recorder({}).adapter()
    with pytest.raises(InputInvalid, match="2–30 s"):
        await adapter.submit(
            Capability.VIDEO_EDIT_V2V, {"video": str(short), "reference_images": [str(image)]}
        )
    notes = tmp_path / "notes.txt"
    notes.write_text("not a video")
    with pytest.raises(InputInvalid, match="Cannot read"):
        await adapter.submit(
            Capability.VIDEO_EDIT_V2V, {"video": str(notes), "reference_images": [str(image)]}
        )


async def test_image_to_video_rounds_duration_up(image):
    path = "/api/v1/services/aigc/video-generation/video-synthesis"
    recorder = Recorder({("POST", path): httpx.Response(200, json={"output": {"task_id": "t2"}})})
    adapter = recorder.adapter("wan2.6-i2v")
    request = {
        "first_frame": str(image),
        "prompt": "The sheep waves",
        "duration": 3.6,
        "negative_prompt": "blurry",
    }
    handle = await adapter.submit(Capability.IMAGE_TO_VIDEO, request)
    assert handle.external_id == "t2"
    body = json.loads(recorder.requests[0].content)
    assert body["parameters"]["duration"] == 4 and body["parameters"]["watermark"] is False
    assert body["input"]["img_url"].startswith("data:image/png;base64,")
    assert body["input"]["negative_prompt"] == "blurry"

    no_task = Recorder({("POST", path): httpx.Response(200, json={"output": {}})}).adapter()
    with pytest.raises(InputInvalid, match="task id"):
        await no_task.submit(Capability.IMAGE_TO_VIDEO, request)


@pytest.mark.parametrize(
    ("output", "status", "error_type"),
    [
        ({"task_status": "RUNNING"}, RemoteStatus.RUNNING, None),
        (
            {"task_status": "FAILED", "code": "DataInspectionFailed", "message": "x"},
            RemoteStatus.FAILED,
            ContentRejected.error_type,
        ),
        (
            {"task_status": "FAILED", "code": "InvalidVideo.FullFace", "message": "no face"},
            RemoteStatus.FAILED,
            "provider_error",
        ),
        ({"task_status": "UNKNOWN"}, RemoteStatus.FAILED, "provider_error"),
    ],
)
def test_task_poll_result(output, status, error_type):
    result = native.task_poll_result({"output": output})
    assert result.status == status and result.error_type == error_type


def test_task_without_video_url():
    with pytest.raises(ProviderError):
        native.task_video_url({"output": {"task_status": "SUCCEEDED"}})
    assert native.task_video_url({"output": {"video_url": "https://v"}}) == "https://v"


async def test_upload_with_a_broken_policy(image):
    recorder = Recorder({("GET", "/api/v1/uploads"): httpx.Response(200, json={"data": {}})})
    async with httpx.AsyncClient(transport=httpx.MockTransport(recorder)) as client:
        with pytest.raises(ProviderError, match="missing"):
            await native.upload_temporary(client, NATIVE, {}, "m", image)


async def test_video_edit_sends_prompt_and_reference_images(image, sample_video):
    path = "/api/v1/services/aigc/video-generation/video-synthesis"
    recorder = Recorder(
        {
            ("GET", "/api/v1/uploads"): httpx.Response(200, json=POLICY),
            ("POST", "/"): httpx.Response(200),
            ("POST", path): httpx.Response(200, json={"output": {"task_id": "t3"}}),
        }
    )
    adapter = recorder.adapter("wan2.7-videoedit")
    request = {
        "video": str(sample_video),
        "reference_images": [str(image), str(image)],
        "mode": "edit",
        "prompt": "give everyone animal heads",
    }
    assert (await adapter.submit(Capability.VIDEO_EDIT_V2V, request)).external_id == "t3"
    body = json.loads(recorder.sent(path).content)
    media = body["input"]["media"]
    assert media[0] == {"type": "video", "url": f"oss://dashscope-instant/abc/{sample_video.name}"}
    assert [m["type"] for m in media[1:]] == ["reference_image", "reference_image"]
    assert media[1]["url"].startswith("data:image/png;base64,")
    assert body["input"]["prompt"] == "give everyone animal heads"
    assert body["parameters"]["resolution"] == "720P"
    assert body["parameters"]["audio_setting"] == "origin"
    # Only the video is uploaded; reference images go inline.
    assert sum(r.url.path == "/api/v1/uploads" for r in recorder.requests) == 1


async def test_video_edit_validation(image, sample_video):
    adapter = Recorder({}).adapter()
    with pytest.raises(InputInvalid, match="prompt"):
        await adapter.submit(
            Capability.VIDEO_EDIT_V2V, {"video": str(sample_video), "mode": "edit"}
        )
    with pytest.raises(InputInvalid, match="reference image"):
        await adapter.submit(
            Capability.VIDEO_EDIT_V2V, {"video": str(sample_video), "mode": "motion"}
        )


async def test_llm_text_uses_chat_completions_with_json_mode():
    path = "/compatible-mode/v1/chat/completions"
    reply = {
        "choices": [{"message": {"content": '{"summary": "ok"}'}}],
        "usage": {"total_tokens": 9},
    }
    recorder = Recorder({("POST", path): httpx.Response(200, json=reply)})
    adapter = recorder.adapter("qwen-text")
    request = {"system": "be brief", "prompt": "hello", "task": "plot_edit"}
    handle = await adapter.submit(Capability.LLM_TEXT, request)
    artifact = await adapter.fetch_result(handle)
    assert artifact.kind == "text" and artifact.data == {"text": '{"summary": "ok"}'}
    body = json.loads(recorder.sent(path).content)
    assert body["messages"] == [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "hello"},
    ]
    assert body["response_format"] == {"type": "json_object"} and body["model"] == "qwen-text"
    with pytest.raises(InputInvalid):
        await adapter.submit(Capability.LLM_TEXT, {"prompt": ""})
