"""Thin async client for the Up Banking API (https://developer.up.com.au/)."""

from __future__ import annotations

import asyncio
import os
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

API_BASE = os.environ.get("UP_API_BASE", "https://api.up.com.au/api/v1")

# Up quotes all times in the customer's local Australian time.
UP_TZ = ZoneInfo("Australia/Sydney")

# The API rejects page[size] above 100.
MAX_PAGE_SIZE = 100


class UpError(Exception):
    """An error returned by the Up API, or a transport failure talking to it."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


def _token() -> str:
    token = os.environ.get("UP_API_TOKEN")
    if not token:
        raise UpError(
            "No Up API token configured. Set UP_API_TOKEN to a personal access "
            "token from https://api.up.com.au/getting_started"
        )
    return token.strip()


def to_rfc3339(value: str | None) -> str | None:
    """Normalise a user-supplied date into the RFC-3339 form Up's filters expect.

    Accepts a bare date ("2025-08-01"), a naive datetime, or an already-qualified
    RFC-3339 string. Bare/naive inputs are anchored to Australia/Sydney, matching
    how Up presents times in the app.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        try:
            parsed = datetime.combine(date.fromisoformat(value), time.min)
        except ValueError as exc:
            raise UpError(
                f"Could not parse {value!r} as a date. Use YYYY-MM-DD or a full "
                "RFC-3339 timestamp such as 2025-08-01T00:00:00+10:00."
            ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UP_TZ)
    return parsed.isoformat()


def _describe_error(response: httpx.Response) -> str:
    """Turn an Up JSON:API error document into a single readable line."""
    try:
        errors = response.json().get("errors", [])
    except ValueError:
        errors = []
    if not errors:
        return f"HTTP {response.status_code} from Up API"
    parts = []
    for err in errors:
        title = err.get("title", "Error")
        detail = err.get("detail", "")
        parts.append(f"{title}: {detail}" if detail else title)
    return f"HTTP {response.status_code} — " + "; ".join(parts)


class UpClient:
    """Async Up API client with retry on rate limits and transient failures."""

    def __init__(self, base_url: str = API_BASE, timeout: float = 30.0, max_retries: int = 3):
        self.base_url = base_url.rstrip("/")
        self.max_retries = max_retries
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"Accept": "application/json", "User-Agent": "upbank-mcp/0.1"},
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> dict[str, Any] | None:
        """Issue a request. `path` may be an API-relative path or a full Up URL
        (as returned in pagination links)."""
        if path.startswith("http://") or path.startswith("https://"):
            # Pagination cursors are opaque URLs; only follow ones on our own host.
            if urlparse(path).netloc != urlparse(self.base_url).netloc:
                raise UpError(f"Refusing to follow off-host URL: {path}")
            url = path
        else:
            url = f"{self.base_url}/{path.lstrip('/')}"

        clean = {k: v for k, v in (params or {}).items() if v is not None}
        headers = {"Authorization": f"Bearer {_token()}"}
        if json is not None:
            headers["Content-Type"] = "application/json"

        last_error: str = "request never attempted"
        for attempt in range(self.max_retries + 1):
            try:
                response = await self._client.request(
                    method, url, params=clean or None, json=json, headers=headers
                )
            except httpx.HTTPError as exc:
                last_error = f"Network error contacting Up API: {exc}"
                if attempt == self.max_retries:
                    raise UpError(last_error) from exc
                await asyncio.sleep(2**attempt)
                continue

            if response.status_code == 429 or response.status_code >= 500:
                last_error = _describe_error(response)
                if attempt == self.max_retries:
                    raise UpError(last_error, response.status_code)
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
                await asyncio.sleep(delay)
                continue

            if response.status_code >= 400:
                raise UpError(_describe_error(response), response.status_code)

            if response.status_code == 204 or not response.content:
                return None
            return response.json()

        raise UpError(last_error)

    async def get(self, path: str, **params: Any) -> dict[str, Any]:
        result = await self.request("GET", path, params=params)
        return result or {}
