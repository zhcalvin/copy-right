# CLAUDE.md

## What this project is

**Copy-Right** (open source, MIT) is a local web app that turns an existing video into an editable storyboard. Users swap characters or scenes, or tweak the plot. The app regenerates only the affected shots and stitches a new video.

All AI work goes through third-party APIs behind pluggable adapters. There are two presets: a China one (百炼、火山方舟、可灵、MiniMax) and a global one (Gemini、Runway、fal、ElevenLabs、Sync Labs). Users can also mix providers per capability.

**The full plan is in `docs/PLAN.md`. Read it before starting any milestone.** It covers:

- architecture
- the 8 capability interfaces
- the provider matrix
- the setup wizard
- the storyboard schema and treatment rules
- the pipeline
- the milestones with acceptance criteria

## Core design (do not drift from this)

- **Shot = source clip + layered description + asset references.** The layers are narrative, visual, control, and assets. The source clip is the source of truth; structured fields are an editable index.
- **Treatment spectrum, lightest first:** `keep → trim → swap → edit → motion → regenerate`, plus `new` and `removed`. It is auto-chosen from `changed_layers`, and the user can override it.
- **Business code never calls a provider SDK directly.** Everything goes through `app/capabilities/base.py` adapters.

## Working rules

1. Implement milestones in order (M0 → M6). Meet each milestone's acceptance criteria and keep `pytest` green before moving on.
2. Never hardcode model IDs or endpoints in code. They live only in `app/providers/registry.yaml`. Before writing a real adapter, check the provider's current official docs for model IDs, endpoints, auth, and input limits. The names in PLAN.md are from mid-2026 and may be stale.
3. Every capability has a `mock` adapter, so the whole flow and UI run with no API keys.
4. Never log or return full secrets. Mask everything except the last 4 characters.
5. `ContentRejected` (provider moderation) is its own error type. The UI must offer a switch to another provider.

## Stack and commands

- Backend: Python 3.11+, FastAPI, SQLModel + SQLite, in-process job worker, uv.
  - `uv sync`
  - `uv run uvicorn app.main:app --reload --port 8000`
  - `uv run pytest` (CI enforces 80% coverage: `uv run pytest --cov=app`)
  - `uv run ruff check app tests && uv run ruff format app tests`
- Frontend: React + Vite + TypeScript + Tailwind v4 in `web/`, proxying `/api` to `localhost:8000`. Use **bun**, never npm or yarn.
  - `cd web && bun install && bun run dev`
  - `cd web && bun run build` (type-checks, then builds)
- System requirement: FFmpeg on PATH.
- Data lives in `data/` (gitignored). Override with `COPYRIGHT_DATA_DIR`.

## Where things are

- Capability interfaces and error types: `app/capabilities/base.py`
- Provider registry and presets: `app/providers/registry.yaml`, loaded by `app/providers/registry.py`
- Mock adapter (all 8 capabilities): `app/providers/mock/adapter.py`
- Credentials (env → encrypted store → default; masked views): `app/providers/credentials.py`, master key in `app/core/crypto.py`
- Free connection checks declared per provider in `registry.yaml` (`connection_test`), run by `app/providers/connection.py`
- Setup wizard and settings UI: `web/src/pages/`, `web/src/setup/`
- Parsing (detect → slice → describe → storyboard v-next): `app/pipeline/parse.py`; shot detection in `app/tools/scene_detect.py`, FFmpeg in `app/tools/media.py`
- `video_understanding` contract (request, lenient result parsing, prompt, chunking): `app/capabilities/video_understanding.py`
- Cost estimates from registry `price` entries: `app/pipeline/estimate.py` (`POST /projects/{id}/renders/estimate`); describe one shot again: `app/pipeline/reparse.py`
- Live-verified adapters: DashScope, MiniMax (speech). Written from docs, unit-tested with mocked HTTP but not yet run with real keys: Gemini generation, Runway, Kling, ElevenLabs, Sync Labs (`tests/test_more_adapters.py`)
- Real adapters: `app/providers/dashscope/` (video understanding, image edit, character swap, image-to-video; native API, temporary `oss://` uploads and async tasks in `native.py`), `app/providers/gemini/` (shared HTTP helpers and error mapping in `app/providers/http.py`)
- Generation request contracts (image edit, video edit, image-to-video): `app/capabilities/generation.py`; clip normalising and stitching: `app/tools/media.py`
- README demo assets (`docs/assets/demo/`) are built from a finished render by `scripts/demo_assets.py` (local FFmpeg only; the render itself is made in the app)
- Project page (upload, parse progress, storyboard board): `web/src/pages/ProjectPage.tsx`, `web/src/project/`
- Storyboard schema and treatment rules: `app/storyboard/schema.py`, `app/storyboard/treatment.py`
- Editing (M3): versions and saves in `app/storyboard/versions.py`; `changed_layers` against the latest parse in `app/storyboard/diff.py`; plot edits via `llm_text` in `app/pipeline/plot_edit.py`; reference images in `app/pipeline/assets.py`; routes in `app/api/storyboard.py`; UI in `web/src/project/` (`ShotEditor`, `AssetPanel`/`AssetEditor`, `PlotEditPanel`, `VersionMenu`)
- Generation (M4): per-shot state machine, frame approval, retries and stitching in `app/pipeline/render.py`; which request each treatment sends (and which video model/mode, padding and splitting by the registry's `min_seconds`/`max_seconds`) in `app/pipeline/render_plan.py`; routes in `app/api/renders.py`; UI in `web/src/project/RenderPanel.tsx`
- Audio (M5): `tts` / `lipsync` contracts in `app/capabilities/audio.py`; a dialogue change sets `edit.voice_changed` (it is not a narrative change) and the render adds tts → lip-sync; stitching ducks the original sound under the new voice. MiniMax speech in `app/providers/minimax/`; voices per provider in `registry.yaml` (`voices`)
- Result reuse: each render shot stores picture/voice signatures; a new render copies matching finished results instead of paying again (`signatures`, `_reuse` in `app/pipeline/render.py`)
- `llm_text` contract (system + prompt in, text out): `app/capabilities/llm_text.py`
- Saving provider results locally (download or copy): `app/pipeline/artifacts.py`; the mock adapter makes real tinted media with FFmpeg (`app/providers/mock/media.py`)
- Job worker (persists external task IDs, resumes after restart, retries `transient` errors with backoff, async hooks): `app/pipeline/worker.py`
- Logo: `docs/assets/logo.png` (full size), `web/public/logo.png` (256px)

## UI language

The UI defaults to **English**, with Simplified Chinese as a switchable option. Every user-facing string goes through `web/src/i18n` (`en.ts` is the source of truth; add the same key to `zh-CN.ts`). Registry display text is English, with Chinese in each entry's `i18n.zh-CN`. API error messages are English; the UI maps `error_type` to localized labels.
