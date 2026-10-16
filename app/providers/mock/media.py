"""Stand-in media for the mock adapter, made locally with FFmpeg.

Results are visibly altered copies of the inputs (a hue shift), so a demo run without keys
still shows which shots were "generated". Without FFmpeg or inputs, a ``mock://`` URI is
returned instead and saving the result fails cleanly.
"""

import logging
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.tools.media import MediaError, _run, ffmpeg_available

logger = logging.getLogger(__name__)

TINT = "hue=h=140:s=1.4"
DEFAULT_SECONDS = 4.0


def _out(name: str) -> Path:
    folder = get_settings().data_dir / "mock"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / name


def _existing(value: Any) -> Path | None:
    path = Path(str(value)) if value else None
    return path if path and path.is_file() else None


def _ffmpeg(args: list[str], out: Path) -> str | None:
    if not ffmpeg_available():
        return None
    try:
        _run(["ffmpeg", "-v", "error", "-y", *args, str(out)])
    except MediaError as exc:
        logger.warning("mock media failed: %s", exc)
        return None
    return str(out)


def image(external_id: str, request: dict[str, Any]) -> str | None:
    source = _existing((request.get("images") or [None])[-1])
    if source is None:
        return None
    return _ffmpeg(["-i", str(source), "-vf", TINT, "-frames:v", "1"], _out(f"{external_id}.png"))


def video(external_id: str, request: dict[str, Any]) -> str | None:
    out = _out(f"{external_id}.mp4")
    clip = _existing(request.get("video"))
    if clip is not None:
        return _ffmpeg(["-i", str(clip), "-vf", TINT, "-an", "-pix_fmt", "yuv420p"], out)
    frame = _existing(request.get("first_frame"))
    if frame is None:
        return None
    seconds = float(request.get("duration") or DEFAULT_SECONDS)
    scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
    return _ffmpeg(
        ["-loop", "1", "-i", str(frame), "-t", f"{seconds:.3f}", "-vf", scale, "-r", "25"]
        + ["-pix_fmt", "yuv420p"],
        out,
    )


def speech(external_id: str, request: dict[str, Any]) -> str | None:
    seconds = max(0.6, min(15.0, len(str(request.get("text", ""))) * 0.07))
    return _ffmpeg(
        ["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds:.2f}"],
        _out(f"{external_id}.wav"),
    )
