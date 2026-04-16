"""Integration tests for the URLs CRUD endpoints."""

import uuid

from starlette.testclient import TestClient


def test_create_url(client: TestClient, mock_fetch_and_clean, mock_compute_embedding):
    response = client.post("/urls/", json={"url": "https://example.com"})
    assert response.status_code == 202
    data = response.json()
    assert "id" in data
    assert len(data) == 1  # only id — full details via GET /urls/{id}


def test_create_url_duplicate(client: TestClient, mock_fetch_and_clean, mock_compute_embedding):
    client.post("/urls/", json={"url": "https://example.com"})
    response = client.post("/urls/", json={"url": "https://example.com"})
    assert response.status_code == 409


def test_create_url_invalid(client: TestClient):
    response = client.post("/urls/", json={"url": "not-a-url"})
    assert response.status_code == 422


def test_list_urls_empty(client: TestClient):
    response = client.get("/urls/")
    assert response.status_code == 200
    assert response.json() == []


def test_list_urls(client: TestClient, mock_fetch_and_clean, mock_compute_embedding):
    client.post("/urls/", json={"url": "https://example.com"})
    client.post("/urls/", json={"url": "https://example.org"})
    response = client.get("/urls/")
    assert response.status_code == 200
    assert len(response.json()) == 2


def test_get_url(client: TestClient, mock_fetch_and_clean, mock_compute_embedding):
    create_resp = client.post("/urls/", json={"url": "https://example.com"})
    url_id = create_resp.json()["id"]
    response = client.get(f"/urls/{url_id}")
    assert response.status_code == 200
    assert response.json()["id"] == url_id


def test_get_url_not_found(client: TestClient):
    response = client.get(f"/urls/{uuid.uuid4()}")
    assert response.status_code == 404


def test_update_url(client: TestClient, mock_fetch_and_clean, mock_compute_embedding):
    create_resp = client.post("/urls/", json={"url": "https://example.com"})
    url_id = create_resp.json()["id"]
    response = client.put(f"/urls/{url_id}", json={"frequency": 600, "status": "inactive"})
    assert response.status_code == 200
    data = response.json()
    assert data["frequency"] == 600
    assert data["status"] == "inactive"


def test_delete_url(client: TestClient, mock_fetch_and_clean, mock_compute_embedding):
    create_resp = client.post("/urls/", json={"url": "https://example.com"})
    url_id = create_resp.json()["id"]
    response = client.delete(f"/urls/{url_id}")
    assert response.status_code == 204
    assert client.get(f"/urls/{url_id}").status_code == 404


def test_delete_url_not_found(client: TestClient):
    response = client.delete(f"/urls/{uuid.uuid4()}")
    assert response.status_code == 404
