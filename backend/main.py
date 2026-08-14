from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional

from database import engine, Base, SessionLocal
import models

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
    db_item = models.Item(title=item.title, type=item.type)
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return db_item


@app.get("/items")
def list_items(db: Session = Depends(get_db)):
    return db.query(models.Item).all()


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