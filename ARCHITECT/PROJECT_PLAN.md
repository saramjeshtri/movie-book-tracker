# Movie & Book Tracker — Project Plan

## What it is
A small web app for a group of friends to track movies/books they want to
watch/read and ones they've finished.

## Team & roles
- Sara (saramjeshtri) — Backend: FastAPI + SQLAlchemy + SQLite, CRUD for /items
- Sindi (sindipopshini22-commits) — External API integration: OMDb + Google
  Books (with Open Library fallback) via GET /fetch-info
- Yahia (Yahia20) — Frontend: add-item form, list view, filter/sort UI

## Data model
Item: id, title, type (movie/book), poster_url, description, year, genre,
status (want_to_watch/watching/finished), rating (1-5)

## Current backend endpoints
- POST /items
- GET /items (filterable by status/type, case-insensitive)
- GET /items/{id}
- PATCH /items/{id}
- DELETE /items/{id}

## Branch strategy
- main: stable, only merged/reviewed work
- feature/backend-continued: Sara's ongoing backend work
- feature/external-api-integration: Sindi's API integration work