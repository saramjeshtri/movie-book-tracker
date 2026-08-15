"""External API integration for Movie & Book Tracker.

Two free sources:
- OMDb (http://www.omdbapi.com/) for movies — requires OMDB_API_KEY.
- Google Books (https://www.googleapis.com/books/v1/volumes) for books — no
  key required, but an optional GOOGLE_BOOKS_API_KEY raises the rate limit.

Public entry point: :func:`fetch_info`. It routes by ``type``, calls the
appropriate provider, and normalizes the result into the shared schema::

    { "title": str, "poster_url": str | None, "description": str | None,
      "year": int | None, "genre": str | None }

Every upstream failure mode (not found, timeout, 5xx, rate limit, bad key)
raises a :class:`FetchError` with an HTTP-style status code so the FastAPI
route can translate it into the right response.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# --- Config -----------------------------------------------------------------

OMDB_BASE_URL = "http://www.omdbapi.com/"
GOOGLE_BOOKS_BASE_URL = "https://www.googleapis.com/books/v1/volumes"

# Per-request timeout. Short enough that a hung upstream doesn't pin the
# FastAPI worker, long enough for a slow but healthy response.
REQUEST_TIMEOUT_SECONDS = 5.0

# Retry policy — only transient network failures (timeouts, connection
# resets, 5xx). "Not found" / 4xx / rate limit are NOT retried.
MAX_RETRIES = 2  # total attempts = 1 initial + 2 retries = 3
RETRY_BACKOFF_SECONDS = 0.5


# --- Errors -----------------------------------------------------------------

class FetchError(Exception):
    """Raised when an external provider can't return data.

    ``status_code`` mirrors an HTTP status so the FastAPI route can re-raise
    it as ``HTTPException(status_code=...)`` without translating strings.
    """

    def __init__(self, message: str, status_code: int):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# --- Normalized record ------------------------------------------------------

@dataclass(frozen=True)
class NormalizedRecord:
    title: str
    poster_url: Optional[str]
    description: Optional[str]
    year: Optional[int]
    genre: Optional[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "poster_url": self.poster_url,
            "description": self.description,
            "year": self.year,
            "genre": self.genre,
        }


# --- Public entry point -----------------------------------------------------

async def fetch_info(title: str, type_: str) -> dict[str, Any]:
    """Look up a movie or book by title and return a normalized dict.

    ``type_`` must be ``"movie"`` or ``"book"`` (case-insensitive). Raises
    :class:`FetchError` on any failure.
    """
    if not title or not title.strip():
        raise FetchError("title is required", 400)

    kind = type_.strip().lower()
    if kind == "movie":
        record = await _fetch_movie(title.strip())
    elif kind == "book":
        record = await _fetch_book(title.strip())
    else:
        raise FetchError(
            f"unsupported type '{type_}' (expected 'movie' or 'book')", 400
        )

    return record.to_dict()


# --- OMDb (movies) ----------------------------------------------------------

def _get_omdb_key() -> str:
    key = os.environ.get("OMDB_API_KEY", "").strip()
    if not key:
        raise FetchError(
            "OMDB_API_KEY environment variable is not set — get a free key "
            "from http://www.omdbapi.com/apikey.aspx and export it",
            500,
        )
    return key


async def _fetch_movie(title: str) -> NormalizedRecord:
    api_key = _get_omdb_key()
    params = {"t": title, "apikey": api_key, "plot": "short"}

    payload = await _get_json_with_retries(
        url=OMDB_BASE_URL,
        params=params,
        # OMDb signals "not found" via Response="False" + Error="Movie not
        # found!" in a 200 response. We map that to a 404 ourselves.
        is_not_found=lambda body: (
            isinstance(body, dict)
            and str(body.get("Response", "")).lower() == "false"
        ),
    )

    # If retries didn't surface it earlier, double-check the flag here too
    # (defense-in-depth in case the heuristic ever drifts).
    if str(payload.get("Response", "True")).lower() == "false":
        err = payload.get("Error", "Movie not found")
        raise FetchError(f"No results found for '{title}'", 404)

    return NormalizedRecord(
        title=_safe_str(payload.get("Title")) or title,
        poster_url=_normalize_poster(payload.get("Poster")),
        description=_safe_str(payload.get("Plot")),
        year=_parse_year(payload.get("Year")),
        genre=_safe_str(payload.get("Genre")),
    )


def _normalize_poster(value: Any) -> Optional[str]:
    """OMDb returns 'N/A' for missing posters — treat that as None.

    Google Books thumbnails come back as ``http://`` URLs; bump to
    ``https://`` so the browser doesn't warn about mixed content.
    """
    s = _safe_str(value)
    if not s or s.upper() == "N/A":
        return None
    return s.replace("http://", "https://", 1) if s.startswith("http://") else s


# --- Google Books -----------------------------------------------------------

def _get_google_books_key() -> Optional[str]:
    return os.environ.get("GOOGLE_BOOKS_API_KEY", "").strip() or None


async def _fetch_book(title: str) -> NormalizedRecord:
    params: dict[str, str] = {"q": title, "maxResults": "1"}
    api_key = _get_google_books_key()
    if api_key:
        params["key"] = api_key

    payload = await _get_json_with_retries(
        url=GOOGLE_BOOKS_BASE_URL,
        params=params,
        is_not_found=lambda body: (
            isinstance(body, dict) and body.get("totalItems", 0) == 0
        ),
    )

    items = payload.get("items") or []
    if not items:
        raise FetchError(f"No results found for '{title}'", 404)

    info = items[0].get("volumeInfo", {}) or {}
    image_links = info.get("imageLinks") or {}

    return NormalizedRecord(
        title=_safe_str(info.get("title")) or title,
        poster_url=_normalize_poster(image_links.get("thumbnail")),
        description=_safe_str(info.get("description")),
        year=_parse_year(info.get("publishedDate")),
        genre=_join_categories(info.get("categories")),
    )


def _join_categories(value: Any) -> Optional[str]:
    """Google Books returns ``categories`` as a list of strings."""
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if v]
        parts = [p for p in parts if p]
        return ", ".join(parts) if parts else None
    return _safe_str(value)


# --- HTTP transport ---------------------------------------------------------

async def _get_json_with_retries(
    *,
    url: str,
    params: dict[str, str],
    is_not_found: Any,
) -> dict[str, Any]:
    """GET ``url?params`` as JSON, retrying transient network errors.

    Non-transient failures (404, 401/403, 429) raise :class:`FetchError`
    immediately — the caller wants those surfaced, not retried.
    """
    attempt = 0
    last_exc: Optional[Exception] = None

    while attempt <= MAX_RETRIES:
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
                response = await client.get(url, params=params)

            # 429 — rate limited. Surface immediately, do not retry.
            if response.status_code == 429:
                raise FetchError(
                    "External API rate limit reached — try again in a moment",
                    429,
                )

            # 401 / 403 — bad key. Surface immediately.
            if response.status_code in (401, 403):
                raise FetchError(
                    "External API rejected the request — check your "
                    "OMDB_API_KEY / GOOGLE_BOOKS_API_KEY",
                    500,
                )

            # 5xx — transient upstream failure. Worth a retry.
            if response.status_code >= 500:
                last_exc = FetchError(
                    f"Upstream API returned {response.status_code}",
                    502,
                )
                await _sleep_backoff(attempt)
                attempt += 1
                continue

            # 4xx (other) — bad request shape, don't retry.
            if response.status_code >= 400:
                raise FetchError(
                    f"Upstream API returned {response.status_code}",
                    502,
                )

            body = response.json()

        except FetchError:
            # Already classified — re-raise.
            raise
        except httpx.TimeoutException as exc:
            last_exc = exc
            logger.warning("Timeout calling %s (attempt %d)", url, attempt + 1)
            await _sleep_backoff(attempt)
            attempt += 1
            continue
        except httpx.HTTPError as exc:
            # Network-level failure: connection reset, DNS, etc.
            last_exc = exc
            logger.warning(
                "Network error calling %s (attempt %d): %s", url, attempt + 1, exc
            )
            await _sleep_backoff(attempt)
            attempt += 1
            continue

        # Successful response — check for soft "not found" markers.
        if is_not_found(body):
            raise FetchError(f"No results found for '{params.get('t') or params.get('q', '?')}'", 404)

        if not isinstance(body, dict):
            raise FetchError("Upstream returned an unexpected response shape", 502)

        return body

    # Exhausted retries.
    if isinstance(last_exc, FetchError):
        raise last_exc
    raise FetchError(
        "External API is unreachable after multiple attempts", 502
    )


async def _sleep_backoff(attempt: int) -> None:
    # Linear backoff is fine here — we only retry a handful of times, and
    # the upstream is one of two well-provisioned public APIs.
    await asyncio.sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))


# --- Field helpers ----------------------------------------------------------

def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        s = value.strip()
        return s if s else None
    return str(value)


_YEAR_RE = re.compile(r"(\d{4})")


def _parse_year(value: Any) -> Optional[int]:
    """Pull the first 4-digit year out of a date-ish string.

    Google Books' ``publishedDate`` can be ``"2010"``, ``"2010-08"``, or
    ``"August 8, 2010"``. OMDb's ``Year`` can be ``"2010"`` or ``"2010–2014"``.
    """
    s = _safe_str(value)
    if not s:
        return None
    match = _YEAR_RE.search(s)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None
