"""Provider-neutral contract for ``llm_text``: one system prompt + one user prompt in,
text out. Callers own their prompts and parse the reply (see :mod:`app.pipeline.plot_edit`).
"""

import json
import re
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from app.capabilities.base import Artifact, InputInvalid


class TextRequest(BaseModel):
    system: str = ""
    prompt: str = Field(min_length=1)
    # Ask for a single JSON object when the provider has a JSON mode.
    json_output: bool = True
    # What the call is for, e.g. "plot_edit". Real providers ignore it; the mock adapter
    # uses it, with ``context``, to return a plausible answer without a model.
    task: str | None = None
    context: dict[str, Any] = Field(default_factory=dict)


def parse_request(raw: dict[str, Any], *, provider: str) -> TextRequest:
    try:
        return TextRequest.model_validate(raw)
    except ValidationError as exc:
        raise InputInvalid(
            f"Bad text request: {exc.errors()[0]['msg']}", provider=provider
        ) from exc


def text_artifact(text: str, **meta: Any) -> Artifact:
    return Artifact(kind="text", data={"text": text}, meta=meta)


_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def json_object(text: str) -> dict[str, Any]:
    """The JSON object in a model reply, tolerating code fences and leading prose.
    Raises ``ValueError`` when there is none."""
    cleaned = _FENCE.sub("", text.strip())
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("The reply contains no JSON object") from None
        try:
            value = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"The reply is not valid JSON: {exc.msg}") from exc
    if not isinstance(value, dict):
        raise ValueError("The reply is not a JSON object")  # noqa: TRY004 (callers catch ValueError)
    return value
