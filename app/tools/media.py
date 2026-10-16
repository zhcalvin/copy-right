"""FFmpeg wrapper: probing, slicing, frame extraction and stitching.

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


def normalize_clip(
    source: Path, out: Path, *, width: int, height: int, fps: float, duration: float
) -> Path:
    """Re-encode a clip to exactly ``width``×``height`` at ``fps`` and ``duration`` seconds,
    without audio. Letterboxes on aspect mismatch; holds the last frame if it is short."""
    out.parent.mkdir(parents=True, exist_ok=True)
    video_filter = ",".join(
        [
            f"scale={width}:{height}:force_original_aspect_ratio=decrease",
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
            "setsar=1",
            f"fps={fps}",
            f"tpad=stop_mode=clone:stop_duration={duration:.3f}",
        ]
    )
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            video_filter,
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )
    return out


def concat_clips(
    clips: list[Path],
    out: Path,
    *,
    audio_source: Path | None = None,
    audio_start: float = 0.0,
) -> Path:
    """Join clips that share size and frame rate (see :func:`normalize_clip`). The audio
    track is taken from ``audio_source`` starting at ``audio_start`` — e.g. the original
    soundtrack, so background sound survives regenerated shots."""
    if not clips:
        raise MediaError("Nothing to join")
    out.parent.mkdir(parents=True, exist_ok=True)
    listing = out.with_suffix(".txt")
    listing.write_text("".join(f"file '{c.resolve()}'\n" for c in clips), encoding="utf-8")
    audio = (
        [
            "-ss",
            f"{audio_start:.3f}",
            "-i",
            str(audio_source),
            "-map",
            "0:v",
            "-map",
            "1:a?",
            "-c:a",
            "aac",
            "-shortest",
        ]
        if audio_source
        else []
    )
    try:
        _run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(listing),
                *audio,
                "-c:v",
                "copy",
                "-movflags",
                "+faststart",
                str(out),
            ]
        )
    finally:
        listing.unlink(missing_ok=True)
    return out


def pad_clip(source: Path, out: Path, *, min_duration: float) -> Path:
    """Hold the last frame so the clip lasts at least ``min_duration`` seconds (providers
    reject very short inputs; the padding is cut off again when stitching)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"tpad=stop_mode=clone:stop_duration={min_duration:.3f}",
            "-t",
            f"{min_duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )
    return out


def split_points(duration: float, max_piece: float) -> list[tuple[float, float]]:
    """Equal pieces no longer than ``max_piece`` covering ``[0, duration)``."""
    if duration <= max_piece:
        return [(0.0, duration)]
    count = -(-duration // max_piece)  # ceil
    step = duration / count
    return [
        (round(i * step, 3), round(min(duration, (i + 1) * step), 3)) for i in range(int(count))
    ]


def _drawtext(text: str) -> str:
    for char in ("\\", ":", "'", "%", ","):
        text = text.replace(char, "\\" + char)
    return (
        f"drawtext=text='{text}':x=w-tw-12:y=h-th-12:fontcolor=white:fontsize=h/24"
        ":box=1:boxcolor=black@0.5:boxborderw=6"
    )


def label_clip(source: Path, out: Path, text: str) -> Path:
    """Burn a small visible label into the bottom-right corner."""
    out.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            _drawtext(text),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )
    return out


@dataclass(frozen=True)
class Segment:
    """One stretch of the output: a normalized clip, and where its sound comes from.
    ``audio_start`` is a time in the source video; ``None`` means silence. ``voice`` is a
    new dialogue track laid over the original sound, which is turned down under it."""

    clip: Path
    duration: float
    audio_start: float | None
    voice: Path | None = None


# How loud the original sound stays under a new voice. Without source separation the old
# dialogue is still in there, so it is ducked rather than kept at full volume.
DUCKED_VOLUME = 0.25


def _audio_graph(segments: list[Segment], has_source: bool, voice_inputs: dict[int, int]) -> str:
    """One audio stretch per segment (source audio, silence, or either with a new voice
    on top), concatenated. ``voice_inputs`` maps a segment index to its ffmpeg input."""
    fmt = "aresample=44100,aformat=channel_layouts=stereo"
    parts, labels = [], []
    for index, seg in enumerate(segments):
        length = f"{seg.duration:.3f}"
        if seg.audio_start is not None and has_source:
            end = seg.audio_start + seg.duration
            base = (
                f"[1:a]atrim=start={seg.audio_start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS,"
                f"{fmt},apad=whole_dur={length}"
            )
        else:
            base = f"anullsrc=r=44100:cl=stereo,atrim=duration={length}"
        if seg.voice is None:
            parts.append(f"{base}[a{index}]")
        else:
            voice = voice_inputs[index]
            parts.append(f"{base},volume={DUCKED_VOLUME}[b{index}]")
            parts.append(
                f"[{voice}:a]{fmt},apad=whole_dur={length},atrim=duration={length}[v{index}]"
            )
            parts.append(f"[b{index}][v{index}]amix=inputs=2:duration=first:normalize=0[a{index}]")
        labels.append(f"[a{index}]")
    return ";".join(parts) + f";{''.join(labels)}concat=n={len(labels)}:v=0:a=1[a]"


def stitch(
    segments: list[Segment],
    out: Path,
    *,
    audio_source: Path | None,
    comment: str | None = None,
) -> Path:
    """Join normalized clips and build the soundtrack segment by segment, so removed or
    new shots keep the remaining audio in sync with the picture."""
    if not segments:
        raise MediaError("Nothing to stitch")
    out.parent.mkdir(parents=True, exist_ok=True)
    video_only = out.with_name(out.stem + ".video.mp4")
    concat_clips([s.clip for s in segments], video_only)
    has_source_audio = audio_source is not None and probe(audio_source).has_audio
    voiced = [i for i, seg in enumerate(segments) if seg.voice is not None]
    if not has_source_audio and not voiced:
        args = ["-i", str(video_only), "-c:v", "copy"]
    else:
        inputs = ["-i", str(video_only)]
        if has_source_audio:
            inputs += ["-i", str(audio_source)]
        first_voice = len(inputs) // 2
        voice_inputs = {seg: first_voice + n for n, seg in enumerate(voiced)}
        for seg in voiced:
            inputs += ["-i", str(segments[seg].voice)]
        graph = _audio_graph(segments, has_source_audio, voice_inputs)
        args = [
            *inputs,
            "-filter_complex",
            graph,
            "-map",
            "0:v",
            "-map",
            "[a]",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
        ]
    metadata = ["-metadata", f"comment={comment}"] if comment else []
    try:
        _run(["ffmpeg", "-v", "error", "-y", *args, *metadata, "-movflags", "+faststart", str(out)])
    finally:
        video_only.unlink(missing_ok=True)
    return out


def fit_video(source: Path, *, min_side: int, min_duration: float) -> Path:
    """A copy of ``source`` scaled up so its shorter side is at least ``min_side`` and
    padded to ``min_duration``, with its audio dropped; ``source`` itself if it fits."""
    info = probe(source)
    short_side = min(info.width, info.height)
    if short_side >= min_side and info.duration >= min_duration:
        return source
    scale = max(1.0, min_side / max(short_side, 1))
    width = int(info.width * scale + 1) // 2 * 2
    height = int(info.height * scale + 1) // 2 * 2
    out = source.with_name(f"{source.stem}.fit.mp4")
    duration = max(info.duration, min_duration)
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"scale={width}:{height},tpad=stop_mode=clone:stop_duration={duration:.3f}",
            "-t",
            f"{duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(out),
        ]
    )
    return out
