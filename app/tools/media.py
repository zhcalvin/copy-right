"""FFmpeg wrapper. M0 only checks availability; slicing and stitching arrive in M2/M4."""

import shutil


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
