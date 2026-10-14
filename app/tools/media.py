"""FFmpeg wrapper: probing, clip slicing and frame extraction.

Every function shells out to ``ffmpeg`` / ``ffprobe`` with an argument list (never a
shell string) and raises :class:`MediaError` with ffmpeg's last stderr line on failure.
"""

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

FFMPEG_TIMEOUT_S = 600
# Frames are pulled from slightly inside a shot so a cut's neighbour never leaks in.
EDGE_MARGIN_S = 0.04


class MediaError(RuntimeError):
    pass


@dataclass(frozen=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _run(args: list[str]) -> str:
    try:
        completed = subprocess.run(
            args, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT_S, check=False
        )
    except FileNotFoundError as exc:
        raise MediaError(f"{args[0]} is not installed or not on PATH") from exc
    except subprocess.TimeoutExpired as exc:
        raise MediaError(f"{args[0]} timed out after {FFMPEG_TIMEOUT_S}s") from exc
    if completed.returncode != 0:
        lines = [line for line in completed.stderr.strip().splitlines() if line.strip()]
        raise MediaError(lines[-1] if lines else f"{args[0]} failed")
    return completed.stdout


def _parse_rate(rate: str) -> float:
    num, _, den = rate.partition("/")
    try:
        value = float(num) / float(den or 1)
    except (ValueError, ZeroDivisionError):
        return 0.0
    return round(value, 3)


def _float(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def probe(path: Path) -> VideoInfo:
    """Read duration, size, frame rate and whether there is an audio track."""
    out = _run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height,avg_frame_rate,r_frame_rate,duration",
            "-of",
            "json",
            str(path),
        ]
    )
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise MediaError("ffprobe returned unreadable output") from exc
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise MediaError("The file has no video stream")
    # The video stream can end before the container (e.g. trailing audio); frames past
    # its end do not exist, so prefer the stream's own duration.
    duration = _float(video.get("duration")) or _float(data.get("format", {}).get("duration"))
    fps = _parse_rate(video.get("avg_frame_rate", "0/0")) or _parse_rate(
        video.get("r_frame_rate", "0/0")
    )
    return VideoInfo(
        duration=round(duration, 3),
        width=int(video.get("width", 0)),
        height=int(video.get("height", 0)),
        fps=fps,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
    )


def cut_clip(source: Path, start: float, end: float, out: Path) -> Path:
    """Re-encode ``[start, end)`` so the clip starts exactly on the cut, not a keyframe."""
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(source),
            "-t",
            f"{max(end - start, 0.001):.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )
    return out


def extract_frame(source: Path, at: float, out: Path, *, max_height: int | None = None) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    scale = ["-vf", f"scale=-2:'min({max_height},ih)'"] if max_height else []
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-ss",
            f"{max(at, 0):.3f}",
            "-i",
            str(source),
            "-frames:v",
            "1",
            *scale,
            "-q:v",
            "4",
            str(out),
        ]
    )
    if not out.exists():
        raise MediaError(f"No frame at {at:.3f}s")
    return out


def sample_times(start: float, end: float, count: int) -> list[float]:
    """``count`` evenly spaced timestamps strictly inside ``[start, end)``."""
    if count < 1:
        return []
    span = end - start
    margin = min(EDGE_MARGIN_S, span / 4)
    inner = max(span - 2 * margin, 0)
    if count == 1:
        return [round(start + span / 2, 3)]
    step = inner / (count - 1)
    return [round(start + margin + i * step, 3) for i in range(count)]
