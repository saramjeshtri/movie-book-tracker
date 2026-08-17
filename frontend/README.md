# Frontend — Person C

Plain HTML/CSS/JS, no build step and no dependencies. Three files:

| File | Purpose |
| --- | --- |
| `index.html` | Page structure: add form, filters, list container |
| `styles.css` | All styling |
| `app.js` | API calls, filtering/sorting, rendering |

## Features

- Add a movie or book by title — details are fetched automatically via `GET /fetch-info`
- List view with poster, year, genre and description
- Change status per item (Want to watch/read → In progress → Finished)
- Star rating 1–5, shown once an item is finished
- Filter by status and type, search by title, sort by newest / title / rating / release year
- Delete with confirmation
- Error banner when the backend or an external API is unavailable

## Running it

The backend must be running first (see `backend/README.md`), then:

```bash
cd frontend
python -m http.server 5500
```

Open <http://127.0.0.1:5500>.

Serve it over HTTP like this rather than double-clicking `index.html` — opening the
file directly gives the page a `file://` origin, which the browser blocks from
calling the API.

If the backend runs somewhere other than `http://127.0.0.1:8000`, change `API_BASE`
at the top of `app.js`.

## Backend changes needed for integration

The frontend is written against the merged backend, but two changes are required in
`backend/main.py` before the full flow works in a browser. Both are verified working.

### 1. CORS — without this the browser blocks every request

```python
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# The frontend is served from a different port, so the browser needs CORS.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
```

### 2. `POST /items` must accept the fetched details

`ItemCreate` currently only accepts `title` and `type`, so the poster, description,
year and genre returned by `GET /fetch-info` are silently discarded and every card
renders bare. `ItemUpdate` only accepts `status` and `rating`, so they cannot be
filled in afterwards either.

```python
class ItemCreate(BaseModel):
    title: str
    type: str
    # Filled in by the frontend from GET /fetch-info.
    poster_url: Optional[str] = None
    description: Optional[str] = None
    year: Optional[int] = None
    genre: Optional[str] = None
    status: Optional[str] = None


@app.post("/items")
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    db_item = models.Item(
        title=item.title,
        type=item.type.lower(),
        poster_url=item.poster_url,
        description=item.description,
        year=item.year,
        genre=item.genre,
        status=item.status or "want_to_watch",
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item
```

## Contract notes

- Status values are `want_to_watch`, `watching`, `finished` — they match
  `models.Item.status`. They are defined once in the `STATUSES` array in `app.js`.
- The `items` table has no `created_at` column, so "Newest first" sorts by `id`.
- Filtering and sorting happen in the browser. `GET /items` also supports
  `?status=` and `?type=`, but for a list this size client-side is instant and
  avoids a round trip per keystroke.
- Movie lookups need `OMDB_API_KEY` set in the backend environment (free key from
  <http://www.omdbapi.com/apikey.aspx>). Book lookups need no key. If a lookup
  fails the item is still added with just its title, and a warning is shown.
