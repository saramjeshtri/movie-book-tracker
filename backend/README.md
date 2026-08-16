# Backend — Movie & Book Tracker API

## Setup
uv sync

## Run the server
uv run uvicorn main:app --reload

## Run tests
uv run pytest

## Endpoints
- POST /items
- GET /items (filter by ?status= and ?type=)
- GET /items/{id}
- PATCH /items/{id}
- DELETE /items/{id}