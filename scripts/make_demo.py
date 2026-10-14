"""Build the README before/after demo from a parsed project.

Shows two treatments on one segment:

- ``swap`` shots keep their motion and only change the character (``video_edit_v2v``);
- ``regenerate`` shots get a new first frame (``image_edit``) that is then animated from
  the shot's description (``image_to_video``), for shots a swap cannot handle, such as
  face close-ups.

Every other shot keeps its original clip. The result is stitched with the original
soundtrack and rendered as a side-by-side comparison (MP4 + GIF) plus before/after stills.

This calls paid provider APIs. Each generated shot is cached under the project's
``results/`` folder, so re-running only re-stitches.

    DASHSCOPE_API_KEY=... uv run python scripts/make_demo.py <project_id> \\
        --shots s007-s012 --swap s007,s012 --regenerate s010 \\
        --character path/to/character.png \\
        --out docs/assets/demo
"""

import argparse
import asyncio
import json
import shutil
import sys
from pathlib import Path

import httpx
from sqlmodel import Session, select

from app.capabilities.base import Adapter, Capability, RemoteStatus
from app.core.db import get_engine
from app.models import Project, Storyboard
from app.providers.credentials import credentials_for
from app.providers.registry import build_adapter
from app.storage.local import project_dir
from app.storyboard.schema import Shot, StoryboardDoc
from app.tools.media import _run, concat_clips, cut_clip, extract_frame, normalize_clip

POLL_S = 15
GIF_WIDTH = 640
GIF_FPS = 8
STILL_W, STILL_H = 480, 360
LABEL = "fontcolor=white:fontsize=h/18:box=1:boxcolor=black@0.55:boxborderw=8"


def shot_range(spec: str, shots: list[Shot]) -> list[Shot]:
    first, _, last = spec.partition("-")
    ids = [s.shot_id for s in shots]
    start, end = ids.index(first), ids.index(last or first)
    return shots[start : end + 1]


EDIT_PROMPT = (
    "Image 1 is a character. Image 2 is a frame from a film. Redraw image 2 with the main "
    "person replaced by the character from image 1, in the same pose, expression, framing, "
    "lighting and background. Keep everything else in image 2 unchanged."
)
MOTION_NOTE = (
    " The person is the cartoon sheep character in a black leather jacket. Keep the camera "
    "framing and stage lighting of the first frame."
)


async def download(url: str, out: Path) -> Path:
    async with httpx.AsyncClient(timeout=300) as client:
        response = await client.get(url)
        response.raise_for_status()
    out.write_bytes(response.content)
    return out


async def run_job(adapter: Adapter, capability: Capability, request: dict, out: Path) -> Path:
    """Submit, poll until done, download. Cached: an existing ``out`` is never re-billed."""
    if out.exists():
        print(f"  {out.name}: cached")
        return out
    handle = await adapter.submit(capability, request)
    print(f"  {out.name}: {capability} {handle.external_id}")
    while True:
        result = await adapter.poll(handle)
        if result.status == RemoteStatus.SUCCEEDED:
            break
        if result.status == RemoteStatus.FAILED:
            raise SystemExit(f"{out.name} failed: {result.error_type}: {result.error}")
        await asyncio.sleep(POLL_S)
    artifact = await adapter.fetch_result(handle)
    await download(artifact.uri or "", out)
    print(f"  {out.name}: saved {artifact.meta.get('usage') or ''}")
    return out


def _escape(text: str) -> str:
    """Escape drawtext's special characters (filter syntax and text expansion)."""
    for char in ("\\", ":", "'", "%", ","):
        text = text.replace(char, "\\" + char)
    return text


def side_by_side(before: Path, after: Path, out: Path, before_label: str, after_label: str) -> None:
    before_label, after_label = _escape(before_label), _escape(after_label)
    graph = (
        f"[0:v]drawtext=text='{before_label}':x=12:y=12:{LABEL}[l];"
        f"[1:v]drawtext=text='{after_label}':x=12:y=12:{LABEL},"
        f"drawtext=text='AI-generated':x=w-tw-12:y=h-th-12:{LABEL}[r];"
        "[l][r]hstack=inputs=2[v]"
    )
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(before),
            "-i",
            str(after),
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-crf",
            "20",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out),
        ]
    )


def gif(source: Path, out: Path) -> None:
    graph = (
        f"fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos,split[a][b];"
        "[a]palettegen=max_colors=96:stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=4"
    )
    _run(["ffmpeg", "-v", "error", "-y", "-i", str(source), "-vf", graph, str(out)])


def stills(pairs: list[tuple[Path, Path, float]], out: Path, work: Path) -> None:
    """One row per swapped shot: original frame | swapped frame."""
    rows = []
    for index, (before, after, at) in enumerate(pairs):
        left = extract_frame(before, at, work / f"still_{index}_a.jpg", max_height=360)
        right = extract_frame(after, at, work / f"still_{index}_b.jpg", max_height=360)
        row = work / f"still_{index}.jpg"
        _run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(left),
                "-i",
                str(right),
                "-filter_complex",
                f"[0]scale={STILL_W}:{STILL_H}[a];[1]scale={STILL_W}:{STILL_H}[b];[a][b]hstack",
                str(row),
            ]
        )
        rows.append(row)
    inputs = [arg for row in rows for arg in ("-i", str(row))]
    _run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            *inputs,
            "-filter_complex",
            f"{''.join(f'[{i}]' for i in range(len(rows)))}vstack=inputs={len(rows)}",
            "-q:v",
            "3",
            str(out),
        ]
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("project_id")
    parser.add_argument("--shots", required=True, help="range of shots to include, e.g. s007-s012")
    parser.add_argument("--swap", default="", help="comma-separated shots to character-swap")
    parser.add_argument("--regenerate", default="", help="comma-separated shots to regenerate")
    parser.add_argument("--character", required=True, type=Path, help="new character image")
    parser.add_argument("--provider", default="dashscope")
    parser.add_argument("--swap-model", default="wan2.2-animate-mix")
    parser.add_argument("--edit-model", default="qwen-image-2.0-pro")
    parser.add_argument("--i2v-model", default="wan2.6-i2v")
    parser.add_argument("--quality", choices=["standard", "pro"], default="pro")
    parser.add_argument("--label", default="Copy-Right: presenter -> sheep")
    parser.add_argument("--out", type=Path, default=Path("docs/assets/demo"))
    args = parser.parse_args()

    engine = get_engine()
    with Session(engine) as session:
        project = session.get(Project, args.project_id)
        if project is None or project.source_video_path is None:
            raise SystemExit("Project not found or has no video")
        latest = session.exec(
            select(Storyboard)
            .where(Storyboard.project_id == project.id)
            .order_by(Storyboard.version.desc())
        ).first()
        if latest is None:
            raise SystemExit("Parse the project first")
        credentials = credentials_for(session, args.provider)
    doc = StoryboardDoc.model_validate(latest.content)
    segment = shot_range(args.shots, doc.shots)
    swap_ids = {s for s in args.swap.split(",") if s}
    regen_ids = {s for s in args.regenerate.split(",") if s}
    root = project_dir(project.id)
    work = root / "results" / "demo"
    work.mkdir(parents=True, exist_ok=True)
    source = Path(project.source_video_path)
    width, height, fps = project.width or 0, project.height or 0, project.fps or 30

    def adapter(model: str) -> Adapter:
        return build_adapter(args.provider, credentials=credentials, model=model)

    swapper, editor, animator = (
        adapter(args.swap_model),
        adapter(args.edit_model),
        adapter(args.i2v_model),
    )
    print(f"swap {sorted(swap_ids)} · regenerate {sorted(regen_ids)}")
    normalized, changed = [], {}
    for shot in segment:
        assert shot.source is not None and shot.source.clip
        clip = root / shot.source.clip
        if shot.shot_id in swap_ids:
            request = {
                "video": str(clip),
                "reference_images": [str(args.character)],
                "quality": args.quality,
            }
            clip = await run_job(
                swapper, Capability.VIDEO_EDIT_V2V, request, work / f"{shot.shot_id}_swap.mp4"
            )
        elif shot.shot_id in regen_ids:
            frame = extract_frame(
                source, shot.source.start + 0.05, work / f"{shot.shot_id}_first.jpg"
            )
            edited = await run_job(
                editor,
                Capability.IMAGE_EDIT,
                {"images": [str(args.character), str(frame)], "prompt": EDIT_PROMPT},
                work / f"{shot.shot_id}_first_edited.png",
            )
            request = {
                "first_frame": str(edited),
                "prompt": shot.narrative.description + MOTION_NOTE,
                "duration": shot.source.end - shot.source.start,
            }
            clip = await run_job(
                animator, Capability.IMAGE_TO_VIDEO, request, work / f"{shot.shot_id}_regen.mp4"
            )
        if clip != root / shot.source.clip:
            changed[shot.shot_id] = clip
        normalized.append(
            normalize_clip(
                clip,
                work / f"{shot.shot_id}_norm.mp4",
                width=width,
                height=height,
                fps=fps,
                duration=shot.source.end - shot.source.start,
            )
        )

    start, end = segment[0].source.start, segment[-1].source.end  # type: ignore[union-attr]
    args.out.mkdir(parents=True, exist_ok=True)
    before = cut_clip(source, start, end, work / "before.mp4")
    after = concat_clips(normalized, args.out / "after.mp4", audio_source=source, audio_start=start)
    compare = args.out / "compare.mp4"
    side_by_side(before, after, compare, "Original", args.label)
    gif(compare, args.out / "compare.gif")
    pairs = [
        (root / s.source.clip, changed[s.shot_id], (s.source.end - s.source.start) / 2)
        for s in segment
        if s.shot_id in changed and s.source and s.source.clip
    ]
    stills(pairs, args.out / "before_after.jpg", work)
    shutil.copy(args.character, args.out / "character.png")
    (args.out / "demo.json").write_text(
        json.dumps(
            {
                "shots": [s.shot_id for s in segment],
                "swapped": sorted(swap_ids),
                "regenerated": sorted(regen_ids),
                "swap_model": args.swap_model,
                "edit_model": args.edit_model,
                "i2v_model": args.i2v_model,
                "quality": args.quality,
                "start": start,
                "end": end,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Wrote {args.out}/: after.mp4, compare.mp4, compare.gif, before_after.jpg")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
