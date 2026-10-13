import json
from pathlib import Path
from typing import Any

SAMPLES_DIR = Path(__file__).parent


def load_sample_storyboard() -> dict[str, Any]:
    return json.loads((SAMPLES_DIR / "sample_storyboard.json").read_text(encoding="utf-8"))
