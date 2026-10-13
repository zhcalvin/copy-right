"""Verify a provider's credentials without triggering billable work.

Implemented adapters use their own ``test_connection()``. Until a provider has one, the
registry's declarative ``connection_test`` request is used instead.
"""

import time
from collections.abc import Mapping
from string import Formatter
from typing import Literal

import httpx
from pydantic import BaseModel

from app.capabilities.base import (
    AuthError,
    ProviderError,
    ProviderTimeout,
    QuotaError,
)
from app.core.secrets import redact
from app.providers.registry import (
    ConnectionTestSpec,
    ProviderSpec,
    StatusOutcome,
    build_adapter,
)

TIMEOUT_S = 15.0
DAY_MS = 24 * 60 * 60 * 1000

DEFAULT_STATUS: dict[int, StatusOutcome] = {
    401: "auth_error",
    403: "auth_error",
    402: "quota_error",
    429: "quota_error",
}

TestStatus = Literal["ok", "failed", "unsupported"]


class ConnectionResult(BaseModel):
    provider: str
    status: TestStatus
    error_type: str | None = None
    message: str | None = None


def _template_vars(credentials: Mapping[str, str]) -> dict[str, str]:
    now_ms = int(time.time() * 1000)
    return {**credentials, "now_ms": str(now_ms), "day_ago_ms": str(now_ms - DAY_MS)}


def _missing_placeholders(spec: ConnectionTestSpec, values: Mapping[str, str]) -> set[str]:
    templates = [spec.url, *spec.headers.values(), *spec.query.values()]
    names = {name for t in templates for _, name, _, _ in Formatter().parse(t) if name}
    return names - set(values)


def _raise_for_status(spec: ConnectionTestSpec, response: httpx.Response, provider: str) -> None:
    code = response.status_code
    outcome = spec.status.get(code) or DEFAULT_STATUS.get(code)
    if outcome == "ok" or (outcome is None and code < 400):
        return
    if outcome == "auth_error":
        raise AuthError(f"Invalid key or no permission (HTTP {code})", provider=provider)
    if outcome == "quota_error":
        raise QuotaError(f"Out of balance or rate limited (HTTP {code})", provider=provider)
    raise ProviderError(f"Provider returned an error (HTTP {code})", provider=provider)


async def run_http_check(
    provider: str,
    spec: ConnectionTestSpec,
    credentials: Mapping[str, str],
    client: httpx.AsyncClient | None = None,
) -> None:
    """Send the registry's check request. Raises a :class:`ProviderError` on failure."""
    values = _template_vars(credentials)
    missing = _missing_placeholders(spec, values)
    if missing:
        raise AuthError(f"Missing credentials: {', '.join(sorted(missing))}", provider=provider)

    url = spec.url.format_map(values)
    headers = {k: v.format_map(values) for k, v in spec.headers.items()}
    params = {k: v.format_map(values) for k, v in spec.query.items()}
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT_S)
    try:
        response = await http.request(spec.method, url, headers=headers, params=params)
    except httpx.TimeoutException as exc:
        raise ProviderTimeout(
            "Connection timed out; check your network or proxy", provider=provider
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            f"Cannot reach the provider: {type(exc).__name__}", provider=provider
        ) from exc
    finally:
        if owns_client:
            await http.aclose()
    _raise_for_status(spec, response, provider)


async def check_provider(
    spec: ProviderSpec,
    credentials: Mapping[str, str],
    client: httpx.AsyncClient | None = None,
) -> ConnectionResult:
    secrets = [credentials[f.name] for f in spec.credentials if f.secret and f.name in credentials]
    try:
        if spec.implemented:
            await build_adapter(spec.id, credentials=dict(credentials)).test_connection()
        elif spec.connection_test is not None:
            await run_http_check(spec.id, spec.connection_test, credentials, client)
        else:
            return ConnectionResult(
                provider=spec.id,
                status="unsupported",
                message="This provider has no connection test yet",
            )
    except ProviderError as exc:
        return ConnectionResult(
            provider=spec.id,
            status="failed",
            error_type=exc.error_type,
            message=redact(str(exc), secrets),
        )
    return ConnectionResult(provider=spec.id, status="ok")
