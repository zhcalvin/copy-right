import pytest

from app.pipeline.estimate import price_of
from app.providers.registry import Price
from tests.test_render import _drain, _stitch, project  # noqa: F401 (fixture)


def test_price_of():
    edit = Price(unit="second", amount=0.6, final=1.0, factor=2)
    assert price_of(edit, 3.6, "preview") == pytest.approx(4.32)
    assert price_of(edit, 3.6, "final") == pytest.approx(7.2)
    i2v = Price(unit="second", amount=0.6, whole_seconds=True, min_quantity=2)
    assert price_of(i2v, 0.4, "preview") == pytest.approx(1.2)
    assert price_of(i2v, 3.2, "preview") == pytest.approx(2.4)
    speech = Price(unit="10k_chars", amount=3.5)
    assert price_of(speech, 2000, "preview") == pytest.approx(0.7)
    assert price_of(None, 1, "preview") is None


def test_estimate_counts_calls_and_reuse(client, project):  # noqa: F811
    url = f"/api/projects/{project}/renders/estimate"
    first = client.post(url, json={"quality": "preview"}).json()
    # Three swapped shots on the mock provider, which lists no prices.
    assert [s["treatment"] for s in first["shots"]] == ["swap", "swap", "swap"]
    assert first["unknown"] == 3 and first["total"] == 0 and first["reused"] == 0
    assert first["shots"][0]["parts"][0]["capability"] == "video_edit_v2v"

    rid = client.post(f"/api/projects/{project}/renders", json={}).json()["id"]
    _drain()
    _stitch(project, rid)
    again = client.post(url, json={"quality": "preview"}).json()
    assert again["reused"] == 3 and again["unknown"] == 0
    assert client.post("/api/projects/missing/renders/estimate", json={}).status_code == 404
