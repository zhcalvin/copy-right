"""Build the README demo from a finished render: side-by-side MP4 and GIF, before/after
stills per changed shot, and a strip of the new cast.

Runs locally with FFmpeg and never calls a provider; the render itself is made in the app.

    COPYRIGHT_DATA_DIR=... uv run python scripts/demo_assets.py <project_id> \\
        [--render <render_id>] --out docs/assets/demo
"""

import argparse
import json
import sys
from pathlib import Path

from sqlmodel import Session, select

from app.core.db import get_engine
from app.models import Project, Render, RenderStatus
from app.storage.local import project_dir
from app.storyboard.versions import load, row_for
from app.tools.media import _run, extract_frame

GIF_WIDTH = 600
GIF_FPS = 7
STILL_W, STILL_H = 400, 300
CAST_SIZE = 220
LABEL = "fontcolor=white:fontsize=h/18:box=1:boxcolor=black@0.55:boxborderw=8"
ENCODE = ["-c:v", "libx264", "-crf", "20", "-preset", "veryfast", "-pix_fmt", "yuv420p"]


def _escape(text: str) -> str:
    for char in ("\\", ":", "'", "%", ","):
        text = text.replace(char, "\\" + char)
    return text


def side_by_side(before: Path, after: Path, out: Path, left: str, right: str) -> None:
    """Both videos are the project's size: renders are normalized to the source."""
    graph = (
        f"[0:v]drawtext=text='{_escape(left)}':x=12:y=12:{LABEL}[l];"
        f"[1:v]drawtext=text='{_escape(right)}':x=12:y=12:{LABEL}[r];"
        "[l][r]hstack=inputs=2[v]"
    )
    _run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(before), "-i", str(after)]
        + ["-filter_complex", graph, "-map", "[v]", "-map", "1:a?", *ENCODE]
        + ["-c:a", "aac", "-movflags", "+faststart", str(out)]
    )


def gif(source: Path, out: Path) -> None:
    graph = (
        f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
    )
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vf", graph, str(out)])


def grid(images: list[Path], out: Path, columns: int, width: int, height: int) -> None:
    inputs = [arg for image in images for arg in ("-i", str(image))]
    scaled = "".join(
        f"[{i}]scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}[s{i}];"
        for i in range(len(images))
    )
    layout = "|".join(
        f"{(i % columns) * width}_{(i // columns) * height}" for i in range(len(images))
    )
    graph = f"{scaled}{''.join(f'[s{i}]' for i in range(len(images)))}xstack=inputs={len(images)}:layout={layout}"
    _run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-q:v", "3", str(out)])


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("project_id")
    parser.add_argument("--render", help="render id (default: the latest finished one)")
    parser.add_argument("--label", default="Copy-Right: new cast")
    parser.add_argument("--out", type=Path, default=Path("docs/assets/demo"))
    args = parser.parse_args()

    with Session(get_engine()) as session:
        project = session.get(Project, args.project_id)
        query = select(Render).where(
            Render.project_id == args.project_id, Render.status == RenderStatus.DONE
        )
        if args.render:
            query = query.where(Render.id == args.render)
        render = session.exec(query.order_by(Render.created_at.desc())).first()
        if (
            project is None
            or render is None
            or render.output is None
            or project.source_video_path is None
        ):
            raise SystemExit("No finished render for that project")
        doc = load(row_for(session, project.id, render.storyboard_version))
    root = project_dir(project.id)
    work = root / "results" / "demo"
    work.mkdir(parents=True, exist_ok=True)
    args.out.mkdir(parents=True, exist_ok=True)
    source, after = Path(project.source_video_path), root / render.output

    compare = args.out / "compare.mp4"
    side_by_side(source, after, compare, "Original", args.label)
    gif(compare, args.out / "compare.gif")

    stills = []
    for shot in doc.shots:
        state = render.shots.get(shot.shot_id, {})
        if not shot.source or state.get("treatment") in (None, "keep", "removed"):
            continue
        middle = (shot.source.start + shot.source.end) / 2
        stills.append(extract_frame(source, middle, work / f"{shot.shot_id}_before.jpg"))
        stills.append(extract_frame(after, middle, work / f"{shot.shot_id}_after.jpg"))
    grid(stills, args.out / "before_after.jpg", columns=4, width=STILL_W, height=STILL_H)

    looks = [root / c.reference_images[0] for c in doc.characters if c.reference_images]
    grid(looks, args.out / "cast.jpg", columns=len(looks), width=CAST_SIZE, height=CAST_SIZE)

    (args.out / "demo.json").write_text(
        json.dumps(
            {
                "storyboard_version": render.storyboard_version,
                "quality": render.quality,
                "shots": {
                    sid: {
                        k: s.get(k) for k in ("treatment", "mode", "voice", "lipsynced", "reused")
                    }
                    for sid, s in render.shots.items()
                },
                "cast": {c.name: c.voice for c in doc.characters if c.reference_images},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {args.out}/: compare.mp4, compare.gif, before_after.jpg, cast.jpg, demo.json")


if __name__ == "__main__":
    sys.exit(main())
