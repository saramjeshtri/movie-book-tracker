# Backend — Movie & Book Tracker API

FastAPI + SQLAlchemy (SQLite) backend.

## Setup

```bash
uv sync --extra dev   # or: pip install -e ".[dev]"
uv run uvicorn main:app --reload   # or: uvicorn main:app --reload
```

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `OMDB_API_KEY` | Yes (movies) | Free key from http://www.omdbapi.com/apikey.aspx — powers `/fetch-info?type=movie` |
| `GOOGLE_BOOKS_API_KEY` | No | Optional; raises the Google Books rate limit for `/fetch-info?type=book`. Books fall back to keyless Open Library when Google Books is unavailable (bad key, rate limit, outage) |

Without `OMDB_API_KEY`, movie lookups fail with a clear 500 error — set it before running.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/` | Health check |
| POST | `/items` | Create an item (`title`, `type` = movie/book) |
| GET | `/items` | List items, filter by `status` / `type` |
| GET | `/items/{id}` | Get one item |
| PATCH | `/items/{id}` | Update `status` / `rating` |
| DELETE | `/items/{id}` | Delete an item |
| GET | `/fetch-info?title=...&type=movie\|book` | External lookup, normalized to `{title, poster_url, description, year, genre}` |

## Tests

```bash
uv run pytest   # mocked HTTP — no API keys or network needed
```
