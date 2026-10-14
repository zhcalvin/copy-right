import json

import pytest

from app.capabilities.base import InputInvalid
from app.capabilities.video_understanding import (
    KnownCharacter,
    ShotInput,
    UnderstandingRequest,
    UnderstandingResult,
    chunks,
    describe_in_chunks,
    instructions,
    parse_request,
    parse_understanding,
    parse_with_retry,
)


def _request(count: int, **extra) -> UnderstandingRequest:
    shots = [
        ShotInput(shot_id=f"s{i:03d}", start=i, end=i + 1, frames=[f"/f/{i}.jpg"])
        for i in range(1, count + 1)
    ]
    return UnderstandingRequest(shots=shots, **extra)


def test_parse_accepts_fenced_and_wrapped_json():
    body = {"storyboard": {"shots": [{"shot_id": "s001", "description": "A"}]}}
    result = parse_understanding(f"```json\n{json.dumps(body)}\n```", provider="p")
    assert result.shots[0].description == "A"


def test_parse_is_lenient_about_messy_items():
    raw = {
        "characters": ["char_1", {"name": "no id"}],
        "shots": [
            {
                "shot_id": 7,
                "description": None,
                "characters": [{}, "char_1", {"character_id": "char_1", "action": 3}],
                "dialogue": ["Hello", {"text": None}],
                "visual": {"shot_size": "Close Up", "camera_motion": None, "lighting": 1},
                "scene_id": "",
            }
        ],
    }
    result = parse_understanding(json.dumps(raw), provider="p")
    shot = result.shots[0]
    assert [c.character_id for c in result.characters] == ["char_1"]
    assert shot.shot_id == "7" and shot.description == "" and shot.scene_id is None
    assert [a.character_id for a in shot.characters] == ["char_1", "char_1"]
    assert shot.characters[1].action is None
    assert [d.text for d in shot.dialogue] == ["Hello"]
    assert shot.visual.shot_size == "close_up" and shot.visual.lighting is None


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("not json", "not valid JSON"),
        ('{"shots": []}', "no shots"),
        ('{"shots": "nope"}', "no shots"),
    ],
)
def test_parse_rejects_unusable_replies(text, message):
    with pytest.raises(InputInvalid, match=message):
        parse_understanding(text, provider="p")


def test_parse_reports_shape_errors():
    text = '{"shots": [{"shot_id": "s1"}], "scenes": [{"scene_id": {"nested": 1}}]}'
    with pytest.raises(InputInvalid, match="does not match"):
        parse_understanding(text, provider="p")


def test_parse_strict_requires_every_shot():
    text = '{"shots": [{"shot_id": "s001"}]}'
    with pytest.raises(InputInvalid, match="skipped"):
        parse_understanding(text, provider="p", expected={"s001", "s002"})


async def test_retry_is_strict_first_then_lenient():
    replies = iter(['{"shots": [{"shot_id": "s001"}]}', '{"shots": [{"shot_id": "s001"}]}'])

    async def fetch() -> str:
        return next(replies)

    result = await parse_with_retry(fetch, provider="p", expected={"s001", "s002"})
    assert [s.shot_id for s in result.shots] == ["s001"]


async def test_retry_gives_up_after_two_bad_replies():
    calls = []

    async def fetch() -> str:
        calls.append(1)
        return "garbage"

    with pytest.raises(InputInvalid):
        await parse_with_retry(fetch, provider="p", expected=set())
    assert len(calls) == 2


async def test_chunks_carry_known_characters_forward():
    seen: list[UnderstandingRequest] = []

    async def call(request: UnderstandingRequest) -> UnderstandingResult:
        seen.append(request)
        index = len(seen)
        return UnderstandingResult.model_validate(
            {
                "characters": [{"character_id": f"char_{index}", "name": f"P{index}"}],
                "shots": [{"shot_id": s.shot_id} for s in request.shots],
            }
        )

    known = [KnownCharacter(character_id="char_0", name="Known")]
    result = await describe_in_chunks(_request(5, characters=known), call, size=2)
    assert [len(r.shots) for r in seen] == [2, 2, 1]
    assert [c.character_id for c in seen[2].characters] == ["char_0", "char_1", "char_2"]
    assert len(result.shots) == 5
    assert {c.character_id for c in result.characters} == {"char_0", "char_1", "char_2", "char_3"}


def test_chunks_and_prompt():
    request = _request(3, language="zh-CN")
    assert [len(c) for c in chunks(request, 2)] == [2, 1]
    prompt = instructions(request)
    assert "Simplified Chinese" in prompt and "medium_close_up" in prompt


def test_parse_request_validates():
    with pytest.raises(InputInvalid, match="Bad video understanding request"):
        parse_request({"shots": []}, provider="p")
    assert parse_request(_request(1).model_dump(), provider="p").shots[0].shot_id == "s001"
