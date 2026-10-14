"""Shot boundary detection with PySceneDetect.

``AdaptiveDetector`` compares each frame with a rolling average, so strobe lights and
fast camera moves cause fewer false cuts than a fixed ``ContentDetector`` threshold.
"""

from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

from app.tools.media import MediaError

# Lower = more cuts. 3.0 is PySceneDetect's default; 2.5 catches sub-second cuts in
# fast-edited footage without splitting on flashes (tuned on a concert recap).
DEFAULT_THRESHOLD = 2.5
MIN_THRESHOLD = 1.0
MAX_THRESHOLD = 6.0
MIN_SHOT_S = 0.2


@dataclass(frozen=True)
class ShotSpan:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return round(self.end - self.start, 3)


def detect_shots(
    path: Path,
    *,
    duration: float,
    fps: float,
    threshold: float = DEFAULT_THRESHOLD,
) -> list[ShotSpan]:
    """Return contiguous shots covering ``[0, duration]``. A video with no cut is one shot."""
    from scenedetect import AdaptiveDetector, detect  # heavy import (OpenCV); load lazily

    min_frames = max(1, round(MIN_SHOT_S * (fps or 30)))
    detector = AdaptiveDetector(adaptive_threshold=threshold, min_scene_len=min_frames)
    try:
        scenes = detect(str(path), detector)
    except Exception as exc:  # PySceneDetect / OpenCV raise a variety of errors
        raise MediaError(f"Shot detection failed: {exc}") from exc
    cuts = sorted({round(start.seconds, 3) for start, _ in scenes[1:]})
    return spans_from_cuts(cuts, duration)


def spans_from_cuts(cuts: list[float], duration: float) -> list[ShotSpan]:
    inner = [c for c in cuts if 0 < c < duration]
    bounds = [0.0, *inner, round(duration, 3)]
    return [ShotSpan(start=a, end=b) for a, b in pairwise(bounds) if b > a]
