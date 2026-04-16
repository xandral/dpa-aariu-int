"""Integration tests for baseline endpoints."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.models import Snapshot, SnapshotKind, Url


def test_get_baseline(client: TestClient, mock_fetch_and_clean, mock_compute_embedding):
    create_resp = client.post("/urls/", json={"url": "https://example.com"})
    url_id = create_resp.json()["id"]

    response = client.get(f"/urls/{url_id}/baseline")
    assert response.status_code == 200
    data = response.json()
    assert data["url_id"] == url_id
    assert data["text_clean"] == "Hello world"


def test_get_baseline_not_found(client: TestClient):
    response = client.get(f"/urls/{uuid.uuid4()}/baseline")
    assert response.status_code == 404



def test_refresh_baseline(
    client: TestClient,
    mock_fetch_and_clean,
    mock_compute_embedding,
    mock_baseline_refresh_fetch,
    mock_baseline_refresh_embedding,
):
    create_resp = client.post("/urls/", json={"url": "https://example.com"})
    url_id = create_resp.json()["id"]

    response = client.post(f"/urls/{url_id}/baseline/refresh")
    assert response.status_code == 200
    data = response.json()
    assert data["text_clean"] == "Updated content"
    assert data["url_id"] == url_id


def test_refresh_baseline_preserves_history(
    client: TestClient,
    db_session: Session,
    mock_fetch_and_clean,
    mock_compute_embedding,
    mock_baseline_refresh_fetch,
    mock_baseline_refresh_embedding,
):
    """Refresh must create a new snapshot without deleting the previous baseline."""
    create_resp = client.post("/urls/", json={"url": "https://example.com"})
    url_id = uuid.UUID(create_resp.json()["id"])

    refresh_resp = client.post(f"/urls/{url_id}/baseline/refresh")
    new_baseline_id = uuid.UUID(refresh_resp.json()["id"])

    all_baselines = (
        db_session.execute(
            select(Snapshot).where(Snapshot.url_id == url_id, Snapshot.kind == SnapshotKind.baseline)
        )
        .scalars()
        .all()
    )
    assert len(all_baselines) == 2, "Old baseline must be preserved after refresh"

    url_obj = db_session.get(Url, url_id)
    assert url_obj.current_baseline_id == new_baseline_id
