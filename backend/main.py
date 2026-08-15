from fastapi import FastAPI, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from database import engine, Base, SessionLocal
import models
from services.external_api import fetch_info, FetchError

# Create all tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI()


# --- DB session dependency ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Request schemas ---
class ItemCreate(BaseModel):
    title: str
    type: str


class ItemUpdate(BaseModel):
    status: Optional[str] = None
    rating: Optional[int] = None


# --- Routes ---
@app.get("/")
def read_root():
    return {"status": "backend is running"}


@app.post("/items")
def create_item(item: ItemCreate, db: Session = Depends(get_db)):
    db_item = models.Item(title=item.title, type=item.type.lower())
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


@app.get("/items")
def list_items(
    status: Optional[str] = None,
    type: Optional[str] = None,
    db: Session = Depends(get_db)
):
    query = db.query(models.Item)
    if status:
        query = query.filter(models.Item.status.ilike(status))
    if type:
        query = query.filter(models.Item.type.ilike(type))
    return query.all()


@app.get("/items/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    return item


@app.patch("/items/{item_id}")
def update_item(item_id: int, update: ItemUpdate, db: Session = Depends(get_db)):
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if update.status is not None:
        item.status = update.status
    if update.rating is not None:
        item.rating = update.rating
    db.commit()
    db.refresh(item)
    return item


@app.delete("/items/{item_id}")
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(models.Item).filter(models.Item.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    db.delete(item)
    db.commit()
    return {"message": "Item deleted"}


@app.get("/fetch-info")
async def fetch_info_endpoint(
    title: str = Query(..., min_length=1, description="Title to look up"),
    type: str = Query(
        ..., pattern="^(?i)(movie|book)$", description="Either 'movie' or 'book'"
    ),
):
    """Look up a movie/book by title via Wikipedia / Open Library.

    Returns the normalized record so the frontend can prefill the create
    form. Errors are translated into HTTP responses the frontend can show.
    """
    try:
        return await fetch_info(title=title, type_=type)
    except FetchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)