"""Helpers that keep secrets out of logs, errors and API responses."""

VISIBLE_SUFFIX = 4
MASK = "****"


def mask_secret(value: str | None) -> str:
    """Show only the last 4 characters of a secret, e.g. ``****abcd``."""
    if not value:
        return ""
    if len(value) <= VISIBLE_SUFFIX:
        return MASK
    return f"{MASK}{value[-VISIBLE_SUFFIX:]}"


def redact(text: str, secrets: list[str]) -> str:
    """Replace every occurrence of each known secret in ``text`` with its masked form."""
    redacted = text
    for secret in sorted((s for s in secrets if s), key=len, reverse=True):
        redacted = redacted.replace(secret, mask_secret(secret))
    return redacted
