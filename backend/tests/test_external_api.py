"""Tests for the external API integration layer.

All HTTP traffic is intercepted via ``respx`` so the tests don't depend on
OMDb, Google Books, or Open Library being reachable. The OMDb API key is
set via env var to satisfy :func:`services.external_api._get_omdb_key`.
"""

from __future__ import annotations

import os

import httpx
import pytest
import respx

# Provide a fake key before importing the module under test so the env
# lookup at call-time sees it.
os.environ.setdefault("OMDB_API_KEY", "test-omdb-key")
os.environ.setdefault("GOOGLE_BOOKS_API_KEY", "test-gbooks-key")

from services.external_api import (  # noqa: E402  (import after env setup)
    GOOGLE_BOOKS_BASE_URL,
    OMDB_BASE_URL,
    OPEN_LIBRARY_BASE_URL,
    FetchError,
    fetch_info,
)

OMDB_OK = {
    "Title": "Inception",
    "Year": "2010",
    "Genre": "Action, Adventure, Sci-Fi",
    "Plot": "A thief who steals corporate secrets...",
    "Poster": "https://m.media-amazon.com/images/inception.jpg",
    "Response": "True",
}

OMDB_NOT_FOUND = {
    "Response": "False",
    "Error": "Movie not found!",
}

OMDB_NO_POSTER = {
    "Title": "Some Obscure Film",
    "Year": "1999",
    "Genre": "Drama",
    "Plot": "...",
    "Poster": "N/A",
    "Response": "True",
}

GBOOKS_OK = {
    "totalItems": 1,
    "items": [
        {
            "volumeInfo": {
                "title": "The Hobbit",
                "publishedDate": "1937-09-21",
                "description": "Bilbo Baggins is a hobbit...",
                "categories": ["Fantasy fiction", "Adventure"],
                "imageLinks": {
                    "thumbnail": "http://books.google.com/hobbit.jpg",
                },
            }
        }
    ],
}

GBOOKS_NOT_FOUND = {"totalItems": 0, "items": []}

# Open Library search result — no ``key`` so the best-effort work-detail
# enrichment call is skipped in the basic fallback tests.
OPENLIB_OK = {
    "numFound": 1,
    "docs": [
        {
            "title": "The Hobbit",
            "first_publish_year": 1937,
            "cover_i": 10520091,
            "subject": ["Fantasy fiction", "Adventure"],
            "first_sentence": [
                "In een hol onder de grond woonde een hobbit.",
                "In a hole in the ground there lived a hobbit.",
            ],
        }
    ],
}


@pytest.mark.asyncio
@respx.mock
async def test_google_books_key_rejected_falls_back_to_openlibrary():
    """Google Books 401/403 (bad/blocked key) → keyless Open Library instead."""
    respx.get(GOOGLE_BOOKS_BASE_URL).mock(
        return_value=httpx.Response(403, json={"error": "blocked"})
    )
    respx.get(OPEN_LIBRARY_BASE_URL).mock(
        return_value=httpx.Response(200, json=OPENLIB_OK)
    )

    record = await fetch_info("The Hobbit", "book")

    assert record == {
        "title": "The Hobbit",
        "poster_url": "https://covers.openlibrary.org/b/id/10520091-M.jpg",
        "description": "In a hole in the ground there lived a hobbit.",
        "year": 1937,
        "genre": "Fantasy fiction, Adventure",
    }


@pytest.mark.asyncio
@respx.mock
async def test_google_books_rate_limited_falls_back_to_openlibrary():
    """Google Books 429 (keyless tier throttling) → Open Library instead."""
    respx.get(GOOGLE_BOOKS_BASE_URL).mock(
        return_value=httpx.Response(429, json={"error": "rate limit"})
    )
    respx.get(OPEN_LIBRARY_BASE_URL).mock(
        return_value=httpx.Response(200, json=OPENLIB_OK)
    )

    record = await fetch_info("The Hobbit", "book")

    assert record["title"] == "The Hobbit"
    assert record["year"] == 1937
    assert record["poster_url"] == (
        "https://covers.openlibrary.org/b/id/10520091-M.jpg"
    )


@pytest.mark.asyncio
@respx.mock
async def test_openlibrary_work_detail_enriches_record():
    """Best-effort work-detail call upgrades description/genre when present."""
    doc_with_key = dict(OPENLIB_OK["docs"][0], key="/works/OL27482W")
    respx.get(GOOGLE_BOOKS_BASE_URL).mock(
        return_value=httpx.Response(403, json={"error": "blocked"})
    )
    respx.get(OPEN_LIBRARY_BASE_URL).mock(
        return_value=httpx.Response(
            200, json={"numFound": 1, "docs": [doc_with_key]}
        )
    )
    respx.get("https://openlibrary.org/works/OL27482W.json").mock(
        return_value=httpx.Response(
            200,
            json={
                "description": {
                    "type": "/type/text",
                    "value": "The Hobbit is a tale of high adventure...",
                },
                "subjects": ["Fantasy", "Hobbits (Fictitious characters)", "Middle Earth"],
            },
        )
    )

    record = await fetch_info("The Hobbit", "book")

    assert record["description"] == "The Hobbit is a tale of high adventure..."
    assert record["genre"] == "Fantasy, Hobbits (Fictitious characters), Middle Earth"


@pytest.mark.asyncio
@respx.mock
async def test_successful_movie_fetch():
    """Happy path: OMDb returns a real movie — fields are normalized."""
    respx.get(OMDB_BASE_URL).mock(
        return_value=httpx.Response(200, json=OMDB_OK)
    )

    record = await fetch_info("Inception", "movie")

    assert record == {
        "title": "Inception",
        "poster_url": "https://m.media-amazon.com/images/inception.jpg",
        "description": "A thief who steals corporate secrets...",
        "year": 2010,
        "genre": "Action, Adventure, Sci-Fi",
    }


@pytest.mark.asyncio
@respx.mock
async def test_successful_book_fetch():
    """Happy path: Google Books returns a real book — categories join into genre."""
    respx.get(GOOGLE_BOOKS_BASE_URL).mock(
        return_value=httpx.Response(200, json=GBOOKS_OK)
    )

    record = await fetch_info("The Hobbit", "book")

    assert record == {
        "title": "The Hobbit",
        "poster_url": "https://books.google.com/hobbit.jpg",
        "description": "Bilbo Baggins is a hobbit...",
        "year": 1937,
        "genre": "Fantasy fiction, Adventure",
    }


@pytest.mark.asyncio
@respx.mock
async def test_title_not_found():
    """Both providers signal 'not found' differently — both must yield 404."""
    # OMDb uses Response="False"
    respx.get(OMDB_BASE_URL).mock(
        return_value=httpx.Response(200, json=OMDB_NOT_FOUND)
    )
    with pytest.raises(FetchError) as exc_info:
        await fetch_info("zzzznonexistent", "movie")
    assert exc_info.value.status_code == 404
    assert "No results found" in exc_info.value.message

    # Google Books uses totalItems=0
    respx.get(GOOGLE_BOOKS_BASE_URL).mock(
        return_value=httpx.Response(200, json=GBOOKS_NOT_FOUND)
    )
    with pytest.raises(FetchError) as exc_info:
        await fetch_info("zzzznonexistent", "book")
    assert exc_info.value.status_code == 404
    assert "No results found" in exc_info.value.message


@pytest.mark.asyncio
@respx.mock
async def test_api_timeout_raises_502():
    """If OMDb hangs past the timeout, surface a 502 — don't crash."""
    respx.get(OMDB_BASE_URL).mock(
        side_effect=httpx.TimeoutException("boom")
    )

    with pytest.raises(FetchError) as exc_info:
        await fetch_info("Inception", "movie")
    assert exc_info.value.status_code == 502
    assert "External API" in exc_info.value.message or "unreachable" in exc_info.value.message


@pytest.mark.asyncio
@respx.mock
async def test_partial_data_no_poster():
    """Missing poster (OMDb 'N/A') should be None, not crash."""
    respx.get(OMDB_BASE_URL).mock(
        return_value=httpx.Response(200, json=OMDB_NO_POSTER)
    )

    record = await fetch_info("Some Obscure Film", "movie")

    assert record["title"] == "Some Obscure Film"
    assert record["poster_url"] is None
    assert record["year"] == 1999


@pytest.mark.asyncio
async def test_missing_omdb_key():
    """No OMDB_API_KEY in env → clear error before any HTTP call."""
    saved = os.environ.pop("OMDB_API_KEY", None)
    try:
        with pytest.raises(FetchError) as exc_info:
            await fetch_info("Inception", "movie")
        assert exc_info.value.status_code == 500
        assert "OMDB_API_KEY" in exc_info.value.message
    finally:
        if saved is not None:
            os.environ["OMDB_API_KEY"] = saved


@pytest.mark.asyncio
async def test_unsupported_type():
    """Unknown type → 400, no upstream call."""
    with pytest.raises(FetchError) as exc_info:
        await fetch_info("Anything", "tv-show")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@respx.mock
async def test_year_extracted_from_various_date_formats():
    """Google Books sometimes returns '2010-08', OMDb '2010-2014' — first 4 digits wins."""
    respx.get(GOOGLE_BOOKS_BASE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "totalItems": 1,
                "items": [
                    {
                        "volumeInfo": {
                            "title": "Partial Date Book",
                            "publishedDate": "2010-08",
                            "imageLinks": {},
                            "categories": [],
                        }
                    }
                ],
            },
        )
    )

    record = await fetch_info("Partial Date Book", "book")
    assert record["year"] == 2010
    assert record["genre"] is None  # empty list joined to nothing
    assert record["poster_url"] is None
