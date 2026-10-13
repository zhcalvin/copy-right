from app.core.secrets import mask_secret, redact


def test_mask_shows_only_last_four():
    assert mask_secret("sk-1234567890abcd") == "****abcd"


def test_mask_hides_short_and_empty_values():
    assert mask_secret("abcd") == "****"
    assert mask_secret("") == ""
    assert mask_secret(None) == ""


def test_redact_replaces_every_known_secret():
    text = "auth failed for sk-secretvalue1234 (backup sk-other99999999)"
    out = redact(text, ["sk-secretvalue1234", "sk-other99999999", ""])
    assert "sk-secretvalue1234" not in out
    assert "sk-other99999999" not in out
    assert "****1234" in out and "****9999" in out
