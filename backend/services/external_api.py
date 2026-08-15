"""External API integration for Movie & Book Tracker.

Two keyless sources:
- Wikipedia REST API (https://en.wikipedia.org/api/rest_v1/) for movies.
- Open Library (https://openlibrary.org/search.json) for books.

Both are free and require no API key. Public entry point:
:func:`fetch_info`. It routes by ``type``, calls the appropriate provider,
and normalizes the result into the shared schema::

    { "title": str, "poster_url": str | None, "description": str | None,
      "year": int | None, "genre": str | None }

Every upstream failure mode (not found, timeout, 5xx, rate limit) raises
:class:`FetchError` with an HTTP-style status code so the FastAPI route
can translate it into the right response.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# --- Config -----------------------------------------------------------------

# Wikipedia REST: trailing path is the (URL-encoded) article title.
# We use the "summary" endpoint which returns a normalized record per page.
WIKIPEDIA_BASE_URL = "https://en.wikipedia.org/api/rest_v1/page/summary"

# Open Library search: q=...&limit=1 returns the single best match.
OPEN_LIBRARY_BASE_URL = "https://openlibrary.org/search.json"

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


# --- Wikipedia (movies) -----------------------------------------------------

async def _fetch_movie(title: str) -> NormalizedRecord:
    # Wikipedia titles are URL path segments — let httpx percent-encode.
    url = f"{WIKIPEDIA_BASE_URL}/{title}"

    payload = await _get_json_with_retries(
        url=url,
        params=None,
        is_not_found=lambda body: False,  # 404 HTTP status handles not-found
        not_found_status_codes=(404,),
    )

    # Wikipedia summary responses look like:
    #   { "type": "standard", "title": "Inception",
    #     "extract": "Inception is a 2010 science-fiction action film ...",
    #     "thumbnail": {"source": "https://.../Inception.jpg", ...},
    #     "description": "2010 film by Christopher Nolan",
    #     "originalimage": {...} }
    #
    # Disambiguation pages come back as type=="disambiguation" with no
    # useful extract — treat as not-found.

    page_type = payload.get("type")
    if page_type == "disambiguation":
        raise FetchError(f"No results found for '{title}'", 404)

    # Wikipedia's "description" string often carries the year, e.g.
    # "2010 film by Christopher Nolan". Use it as a year source but only
    # if the explicit year field is missing.
    year = _parse_year(payload.get("description"))
    # Some pages also expose a "film_release_year" via coordinates? No —
    # safer to fall back to the first 4-digit year in the description.

    return NormalizedRecord(
        title=_safe_str(payload.get("title")) or title,
        poster_url=_extract_thumbnail(payload.get("thumbnail"))
            or _extract_thumbnail(payload.get("originalimage")),
        description=_safe_str(payload.get("extract")),
        year=year,
        genre=None,  # Wikipedia summary doesn't expose genres
    )


def _extract_thumbnail(image_obj: Any) -> Optional[str]:
    if not isinstance(image_obj, dict):
        return None
    src = image_obj.get("source")
    if not src:
        return None
    # Wikipedia returns http:// thumbnails; bump to https:// so the browser
    # doesn't warn about mixed content.
    return src.replace("http://", "https://", 1) if src.startswith("http://") else src


# --- Open Library (books) ---------------------------------------------------

async def _fetch_book(title: str) -> NormalizedRecord:
    params = {"q": title, "limit": "1"}

    payload = await _get_json_with_retries(
        url=OPEN_LIBRARY_BASE_URL,
        params=params,
        is_not_found=lambda body: (
            isinstance(body, dict) and int(body.get("numFound", 0)) == 0
        ),
    )

    docs = payload.get("docs") or []
    if not docs:
        raise FetchError(f"No results found for '{title}'", 404)

    doc = docs[0]
    cover_id = doc.get("cover_i")
    poster_url = (
        f"https://covers.openlibrary.org/b/id/{cover_id}-M.jpg"
        if cover_id is not None
        else None
    )

    return NormalizedRecord(
        title=_safe_str(doc.get("title")) or title,
        poster_url=poster_url,
        description=_safe_str(doc.get("first_sentence"))
            or _join_subjects(doc.get("subject")),  # fall back to subjects as blurb
        year=_parse_year(doc.get("first_publish_year")),
        genre=_join_subjects(doc.get("subject")),
    )


def _join_subjects(value: Any, max_items: int = 5) -> Optional[str]:
    """Open Library returns ``subject`` as a list of strings — cap and join."""
    if isinstance(value, list):
        parts = [str(v).strip() for v in value if v]
        parts = [p for p in parts if p]
        if not parts:
            return None
        return ", ".join(parts[:max_items])
    return _safe_str(value)


# --- HTTP transport ---------------------------------------------------------

async def _get_json_with_retries(
    *,
    url: str,
    params: Optional[dict[str, str]],
    is_not_found: Any,
    not_found_status_codes: tuple[int, ...] = (),
) -> dict[str, Any]:
    """GET ``url[?params]`` as JSON, retrying transient network errors.

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

            # 401 / 403 — bad auth (not relevant for our keyless APIs, but
            # defensive in case Wikipedia ever requires one).
            if response.status_code in (401, 403):
                raise FetchError(
                    "External API rejected the request",
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

            # Explicit "not found" codes (e.g. Wikipedia 404 for missing
            # articles) — surface immediately, no retry.
            if response.status_code in not_found_status_codes:
                title_hint = (
                    (params or {}).get("q")
                    or url.rsplit("/", 1)[-1]
                )
                raise FetchError(
                    f"No results found for '{title_hint}'", 404
                )

            # 4xx (other) — bad request shape, don't retry.
            if response.status_code >= 400:
                raise FetchError(
                    f"Upstream API returned {response.status_code}",
                    502,
                )

            body = response.json()

        except FetchError:
            raise
        except httpx.TimeoutException as exc:
            last_exc = exc
            logger.warning("Timeout calling %s (attempt %d)", url, attempt + 1)
            await _sleep_backoff(attempt)
            attempt += 1
            continue
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning(
                "Network error calling %s (attempt %d): %s", url, attempt + 1, exc
            )
            await _sleep_backoff(attempt)
            attempt += 1
            continue

        if is_not_found(body):
            title_hint = (
                (params or {}).get("q")
                or url.rsplit("/", 1)[-1]
            )
            raise FetchError(f"No results found for '{title_hint}'", 404)

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
    """Pull the first 4-digit year out of a date-ish string or number.

    Open Library returns ``first_publish_year`` as an int. Wikipedia's
    ``description`` is free text like ``"2010 film by Christopher Nolan"``.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            year = int(value)
            return year if 1800 <= year <= 2100 else None
        except (ValueError, TypeError):
            return None
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
