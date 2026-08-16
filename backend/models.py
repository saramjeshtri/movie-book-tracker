from sqlalchemy import Column, Integer, String, Float
from database import Base

class Item(Base):
    __tablename__ = "items"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    type = Column(String, nullable=False)  # "movie" or "book"
    poster_url = Column(String, nullable=True)
    description = Column(String, nullable=True)
    year = Column(Integer, nullable=True)
    genre = Column(String, nullable=True)
    status = Column(String, default="want_to_watch")  # want_to_watch / watching / finished
    rating = Column(Integer, nullable=True)  # 1-5, only set once finished