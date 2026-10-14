"""DashScope native API (``/api/v1``): temporary file upload, image edit, async video tasks.

Checked against the Model Studio docs on 2026-10-14:
- temporary upload: https://help.aliyun.com/zh/model-studio/get-temporary-file-url
  (GET /uploads?action=getPolicy, POST the file to OSS, use ``oss://<key>`` for 48 h
  with the header ``X-DashScope-OssResourceResolve: enable``);
- image edit: https://help.aliyun.com/zh/model-studio/qwen-image-edit-api
  (POST /services/aigc/multimodal-generation/generation, 1–3 images + 1 text);
- character swap: https://help.aliyun.com/zh/model-studio/wan-animate-mix-api
  (POST /services/aigc/image2video/video-synthesis with ``X-DashScope-Async: enable``,
  poll GET /tasks/{id}; clips must be 2–30 s).
"""

from pathlib import Path
from typing import Any

import httpx

from app.capabilities.base import (
    ContentRejected,
    InputInvalid,
    PollResult,
    ProviderError,
    RemoteStatus,
)
from app.providers.http import send

COMPATIBLE_SUFFIX = "/compatible-mode/v1"
NATIVE_SUFFIX = "/api/v1"

TASK_STATUS = {
    "PENDING": RemoteStatus.PENDING,
    "RUNNING": RemoteStatus.RUNNING,
    "SUCCEEDED": RemoteStatus.SUCCEEDED,
    "FAILED": RemoteStatus.FAILED,
    "CANCELED": RemoteStatus.FAILED,
    "UNKNOWN": RemoteStatus.FAILED,  # the task id expired (24 h)
}
MODERATION_CODES = ("DataInspectionFailed", "data_inspection_failed", "IPInfringementSuspect")


def native_base(base_url: str) -> str:
    """The configured base URL points at the OpenAI-compatible API; derive the native one."""
    root = base_url.rstrip("/")
    return root.removesuffix(COMPATIBLE_SUFFIX).removesuffix(NATIVE_SUFFIX) + NATIVE_SUFFIX


async def upload_temporary(
    client: httpx.AsyncClient, base: str, headers: dict[str, str], model: str, path: Path
) -> str:
    """Upload a local file for ``model`` and return its ``oss://`` URL."""
    policy_response = await send(
        client,
        "dashscope",
        "GET",
        f"{base}/uploads",
        params={"action": "getPolicy", "model": model},
        headers=headers,
    )
    policy = policy_response.json().get("data") or {}
    try:
        key = f"{policy['upload_dir']}/{path.name}"
        form = {
            "OSSAccessKeyId": policy["oss_access_key_id"],
            "Signature": policy["signature"],
            "policy": policy["policy"],
            "x-oss-object-acl": policy["x_oss_object_acl"],
            "x-oss-forbid-overwrite": policy["x_oss_forbid_overwrite"],
            "key": key,
            "success_action_status": "200",
        }
        host = policy["upload_host"]
    except KeyError as exc:
        raise ProviderError(f"Upload policy is missing {exc}", provider="dashscope") from exc
    with path.open("rb") as handle:
        await send(
            client, "dashscope", "POST", host, data=form, files={"file": (path.name, handle)}
        )
    return f"oss://{key}"


def task_poll_result(body: dict[str, Any]) -> PollResult:
    output = body.get("output") or {}
    status = TASK_STATUS.get(output.get("task_status", ""), RemoteStatus.FAILED)
    if status != RemoteStatus.FAILED:
        return PollResult(status=status)
    code = str(output.get("code") or "")
    message = str(output.get("message") or output.get("task_status") or "Task failed")
    rejected = any(marker in code for marker in MODERATION_CODES)
    return PollResult(
        status=RemoteStatus.FAILED,
        error_type=ContentRejected.error_type if rejected else "provider_error",
        error=f"{code}: {message}" if code else message,
    )


def task_video_url(body: dict[str, Any]) -> str:
    output = body.get("output") or {}
    url = (output.get("results") or {}).get("video_url") or output.get("video_url")
    if not url:
        raise ProviderError("The finished task has no video URL", provider="dashscope")
    return url


def edited_image_urls(body: dict[str, Any]) -> list[str]:
    try:
        content = body["output"]["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderError("Image edit reply has no images", provider="dashscope") from exc
    urls = [part["image"] for part in content if isinstance(part, dict) and part.get("image")]
    if not urls:
        raise InputInvalid("Image edit returned no image", provider="dashscope")
    return urls
