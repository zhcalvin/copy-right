import pytest

from app.tools.media import MediaError, cut_clip, extract_frame, probe, sample_times
from app.tools.scene_detect import detect_shots, spans_from_cuts
from tests.conftest import SAMPLE_CUTS, SAMPLE_DURATION


def test_probe(sample_video):
    info = probe(sample_video)
    assert info.duration == pytest.approx(SAMPLE_DURATION, abs=0.05)
    assert (info.width, info.height, info.fps) == (160, 120, 30.0)
    assert info.has_audio is False


def test_probe_rejects_non_video(tmp_path):
    bogus = tmp_path / "notes.mp4"
    bogus.write_text("not a video")
    with pytest.raises(MediaError):
        probe(bogus)


def test_cut_clip_and_frames(sample_video, tmp_path):
    clip = cut_clip(sample_video, 1.0, 2.0, tmp_path / "clips" / "s002.mp4")
    assert probe(clip).duration == pytest.approx(1.0, abs=0.05)
    frame = extract_frame(sample_video, 1.5, tmp_path / "f.jpg", max_height=60)
    assert frame.stat().st_size > 0


def test_extract_frame_past_the_end_fails(sample_video, tmp_path):
    with pytest.raises(MediaError):
        extract_frame(sample_video, 99, tmp_path / "late.jpg")


def test_sample_times_stay_inside_the_shot():
    times = sample_times(1.0, 2.0, 3)
    assert len(times) == 3
    assert all(1.0 < t < 2.0 for t in times)
    assert times == sorted(times)
    assert sample_times(0, 1, 1) == [0.5]
    assert sample_times(0, 1, 0) == []


def test_detect_shots_finds_each_cut(sample_video):
    spans = detect_shots(sample_video, duration=SAMPLE_DURATION, fps=30)
    assert len(spans) == 3
    for span, cut in zip(spans[1:], SAMPLE_CUTS, strict=True):
        assert span.start == pytest.approx(cut, abs=0.05)  # well inside the 0.5 s target
    assert spans[0].start == 0
    assert spans[-1].end == SAMPLE_DURATION


def test_detect_shots_on_unreadable_file(tmp_path):
    with pytest.raises(MediaError):
        detect_shots(tmp_path / "missing.mp4", duration=1, fps=30)


def test_spans_from_cuts_ignores_out_of_range_cuts():
    spans = spans_from_cuts([0.0, 1.0, 5.0], 2.0)
    assert [(s.start, s.end) for s in spans] == [(0.0, 1.0), (1.0, 2.0)]
    assert spans[0].duration == 1.0
    assert len(spans_from_cuts([], 2.0)) == 1
