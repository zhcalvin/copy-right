"""Save a finished job's media where the project keeps it.

Providers return short-lived download URLs (DashScope's expire after 24 h), so results are
copied into the project folder as soon as a job finishes. The mock adapter returns local
paths instead, which are copied the same way.
"""

import shutil
from pathlib import Path
from typing import Any

import httpx

DOWNLOAD_TIMEOUT_S = 300.0


class ArtifactMissing(Exception):
    pass


async def save_artifact(result: dict[str, Any] | None, out: Path) -> Path:
    """Copy a job result's ``uri`` to ``out``. Raises :class:`ArtifactMissing`."""
    uri = str((result or {}).get("uri") or "")
    out.parent.mkdir(parents=True, exist_ok=True)
    if uri.startswith(("http://", "https://")):
        try:
            async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S, follow_redirects=True) as c:
                response = await c.get(uri)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ArtifactMissing(f"Downloading the result failed: {type(exc).__name__}") from exc
        out.write_bytes(response.content)
        return out
    local = Path(uri)
    if uri and local.is_file():
        shutil.copyfile(local, out)
        return out
    raise ArtifactMissing("The job finished without a downloadable file")


def suffix_for(result: dict[str, Any] | None, default: str) -> str:
    mime = str((result or {}).get("mime_type") or "")
    return {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
        "video/mp4": ".mp4",
        "audio/wav": ".wav",
        "audio/mpeg": ".mp3",
    }.get(mime, default)
