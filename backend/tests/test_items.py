import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base
import main
from main import app, get_db

TEST_DATABASE_URL = "sqlite:///:memory:"

engine = create_engine(
    TEST_DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db

client = TestClient(app)


@pytest.fixture(autouse=True)
def reset_db():
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


def create_item(title="Dune", type="book"):
    return client.post("/items", json={"title": title, "type": type})


def test_create_item():
    response = create_item(title="Dune", type="Book")

    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Dune"
    assert data["type"] == "book"
    assert data["status"] == "want_to_watch"
    assert data["rating"] is None
    assert "id" in data


def test_list_items_empty():
    response = client.get("/items")

    assert response.status_code == 200
    assert response.json() == []


def test_list_items_returns_created_items():
    create_item(title="Dune", type="book")
    create_item(title="Inception", type="movie")

    response = client.get("/items")

    assert response.status_code == 200
    titles = {item["title"] for item in response.json()}
    assert titles == {"Dune", "Inception"}


def test_list_items_filters_by_type():
    create_item(title="Dune", type="book")
    create_item(title="Inception", type="movie")

    response = client.get("/items", params={"type": "movie"})

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Inception"


def test_list_items_filters_by_type_case_insensitive():
    create_item(title="Dune", type="book")

    response = client.get("/items", params={"type": "BOOK"})

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Dune"


def test_list_items_filters_by_status():
    created = create_item(title="Dune", type="book").json()
    create_item(title="Inception", type="movie")
    client.patch(f"/items/{created['id']}", json={"status": "finished"})

    response = client.get("/items", params={"status": "finished"})

    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["title"] == "Dune"


def test_get_item():
    created = create_item(title="Dune", type="book").json()

    response = client.get(f"/items/{created['id']}")

    assert response.status_code == 200
    assert response.json()["title"] == "Dune"


def test_get_item_not_found():
    response = client.get("/items/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Item not found"


def test_update_item_status():
    created = create_item(title="Dune", type="book").json()

    response = client.patch(f"/items/{created['id']}", json={"status": "watching"})

    assert response.status_code == 200
    assert response.json()["status"] == "watching"


def test_update_item_rating():
    created = create_item(title="Dune", type="book").json()

    response = client.patch(f"/items/{created['id']}", json={"rating": 5})

    assert response.status_code == 200
    assert response.json()["rating"] == 5


def test_update_item_partial_update_preserves_other_fields():
    created = create_item(title="Dune", type="book").json()
    client.patch(f"/items/{created['id']}", json={"rating": 4})

    response = client.patch(f"/items/{created['id']}", json={"status": "finished"})

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "finished"
    assert data["rating"] == 4


def test_update_item_not_found():
    response = client.patch("/items/999", json={"status": "finished"})

    assert response.status_code == 404
    assert response.json()["detail"] == "Item not found"


def test_delete_item():
    created = create_item(title="Dune", type="book").json()

    response = client.delete(f"/items/{created['id']}")

    assert response.status_code == 200
    assert response.json() == {"message": "Item deleted"}

    follow_up = client.get(f"/items/{created['id']}")
    assert follow_up.status_code == 404


def test_delete_item_not_found():
    response = client.delete("/items/999")

    assert response.status_code == 404
    assert response.json()["detail"] == "Item not found"
