"""Tests for the external API integration layer.

All HTTP traffic is intercepted via ``respx`` so the tests don't depend on
Wikipedia or Open Library being reachable. Both providers are keyless, so
no env-var setup is needed.
"""

from __future__ import annotations

import re

import httpx
import pytest
import respx

from services.external_api import (
    OPEN_LIBRARY_BASE_URL,
    WIKIPEDIA_BASE_URL,
    FetchError,
    fetch_info,
)

# respx's default URL matcher is strict about percent-encoding — the real
# outgoing URL contains %20 for spaces. Using a regex lets every
# Wikipedia call get matched regardless of title.
WIKI_URL_PATTERN = re.compile(rf"^{re.escape(WIKIPEDIA_BASE_URL)}/.*$")

WIKI_OK = {
    "type": "standard",
    "title": "Inception",
    "extract": "Inception is a 2010 science-fiction action film...",
    "description": "2010 film by Christopher Nolan",
    "thumbnail": {
        "source": "https://upload.wikimedia.org/.../Inception.jpg",
        "width": 220,
        "height": 326,
    },
}

WIKI_NO_THUMB = {
    "type": "standard",
    "title": "Some Obscure Film",
    "extract": "...",
    "description": "1999 short film",
}

WIKI_DISAMBIG = {
    "type": "disambiguation",
    "title": "Mercury",
    "extract": "Mercury may refer to: ...",
}

OPENLIB_OK = {
    "numFound": 1,
    "docs": [
        {
            "title": "The Hobbit",
            "first_publish_year": 1937,
            "cover_i": 10520091,
            "subject": ["Fantasy fiction", "Adventure", "Middle Earth"],
            "first_sentence": "In a hole in the ground there lived a hobbit.",
        }
    ],
}

OPENLIB_NO_RESULTS = {"numFound": 0, "docs": []}


@pytest.mark.asyncio
@respx.mock
async def test_successful_movie_fetch():
    """Wikipedia summary → normalized movie record."""
    respx.get(WIKI_URL_PATTERN).mock(
        return_value=httpx.Response(200, json=WIKI_OK)
    )

    record = await fetch_info("Inception", "movie")

    assert record == {
        "title": "Inception",
        "poster_url": "https://upload.wikimedia.org/.../Inception.jpg",
        "description": "Inception is a 2010 science-fiction action film...",
        "year": 2010,
        "genre": None,
    }


@pytest.mark.asyncio
@respx.mock
async def test_successful_book_fetch():
    """Open Library search → cover URL built from cover_i, subjects joined."""
    respx.get(OPEN_LIBRARY_BASE_URL).mock(
        return_value=httpx.Response(200, json=OPENLIB_OK)
    )

    record = await fetch_info("The Hobbit", "book")

    assert record == {
        "title": "The Hobbit",
        "poster_url": "https://covers.openlibrary.org/b/id/10520091-M.jpg",
        "description": "In a hole in the ground there lived a hobbit.",
        "year": 1937,
        "genre": "Fantasy fiction, Adventure, Middle Earth",
    }


@pytest.mark.asyncio
@respx.mock
async def test_title_not_found():
    """Both providers signal 'not found' differently — both yield 404."""

    # Wikipedia: 404 HTTP status for missing articles.
    respx.get(WIKI_URL_PATTERN).mock(
        return_value=httpx.Response(404, json={"detail": "Not found."})
    )
    with pytest.raises(FetchError) as exc_info:
        await fetch_info("zzzznonexistent", "movie")
    assert exc_info.value.status_code == 404
    assert "No results found" in exc_info.value.message

    # Wikipedia: also a 200 response with type=="disambiguation".
    respx.get(WIKI_URL_PATTERN).mock(
        return_value=httpx.Response(200, json=WIKI_DISAMBIG)
    )
    with pytest.raises(FetchError) as exc_info:
        await fetch_info("Mercury", "movie")
    assert exc_info.value.status_code == 404

    # Open Library: numFound=0 in a 200 response.
    respx.get(OPEN_LIBRARY_BASE_URL).mock(
        return_value=httpx.Response(200, json=OPENLIB_NO_RESULTS)
    )
    with pytest.raises(FetchError) as exc_info:
        await fetch_info("zzzznonexistent", "book")
    assert exc_info.value.status_code == 404
    assert "No results found" in exc_info.value.message


@pytest.mark.asyncio
@respx.mock
async def test_api_timeout_raises_502():
    """If Wikipedia hangs past the timeout, surface a 502 — don't crash."""
    respx.get(WIKI_URL_PATTERN).mock(
        side_effect=httpx.TimeoutException("boom")
    )

    with pytest.raises(FetchError) as exc_info:
        await fetch_info("Inception", "movie")
    assert exc_info.value.status_code == 502
    assert "unreachable" in exc_info.value.message.lower()


@pytest.mark.asyncio
@respx.mock
async def test_partial_data_no_thumbnail():
    """Wikipedia page with no thumbnail → poster_url is None, no crash."""
    respx.get(WIKI_URL_PATTERN).mock(
        return_value=httpx.Response(200, json=WIKI_NO_THUMB)
    )

    record = await fetch_info("Some Obscure Film", "movie")

    assert record["title"] == "Some Obscure Film"
    assert record["poster_url"] is None
    assert record["year"] == 1999
    assert record["genre"] is None


@pytest.mark.asyncio
async def test_unsupported_type():
    """Unknown type → 400, no upstream call."""
    with pytest.raises(FetchError) as exc_info:
        await fetch_info("Anything", "tv-show")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
@respx.mock
async def test_openlibrary_subject_list_capped():
    """Subject list is capped (5) to keep genre field readable."""
    many_subjects = [f"Subject {i}" for i in range(20)]
    respx.get(OPEN_LIBRARY_BASE_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "numFound": 1,
                "docs": [
                    {
                        "title": "Many Subjects Book",
                        "first_publish_year": 2020,
                        "subject": many_subjects,
                    }
                ],
            },
        )
    )

    record = await fetch_info("Many Subjects Book", "book")
    assert record["genre"] is not None
    assert record["genre"].count(",") == 4  # 5 items → 4 commas
    assert record["genre"].startswith("Subject 0,")
