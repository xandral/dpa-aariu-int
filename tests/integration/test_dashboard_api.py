"""Integration tests for the dashboard endpoint."""

import uuid

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.models import CheckStatus, Snapshot, SnapshotKind


def _insert_check(db: Session, url_id: uuid.UUID, status: CheckStatus) -> None:
    result = Snapshot(
        url_id=url_id,
        kind=SnapshotKind.check,
        html_raw="<html></html>",
        text_clean="text",
        diff_percentage=5.0,
        similarity_score=None,
        status=status,
        llm_analysis=None,
    )
    db.add(result)
    db.commit()


def test_dashboard_empty(client: TestClient):
    response = client.get("/dashboard/")
    assert response.status_code == 200
    data = response.json()
    assert data["total_urls"] == 0
    assert data["ok"] == 0
    assert data["no_check_yet"] == 0


def test_dashboard_no_checks_yet(client: TestClient, mock_fetch_and_clean, mock_compute_embedding):
    client.post("/urls/", json={"url": "https://example.com"})
    response = client.get("/dashboard/")
    assert response.status_code == 200
    data = response.json()
    assert data["total_urls"] == 1
    assert data["no_check_yet"] == 1
    assert data["ok"] == 0


def test_dashboard_counts(
    client: TestClient, db_session: Session, mock_fetch_and_clean, mock_compute_embedding
):
    r1 = client.post("/urls/", json={"url": "https://example.com"})
    r2 = client.post("/urls/", json={"url": "https://example.org"})
    client.post("/urls/", json={"url": "https://example.net"})

    id1 = uuid.UUID(r1.json()["id"])
    id2 = uuid.UUID(r2.json()["id"])

    _insert_check(db_session, id1, CheckStatus.OK)
    _insert_check(db_session, id2, CheckStatus.ALERT)

    response = client.get("/dashboard/")
    assert response.status_code == 200
    data = response.json()
    assert data["total_urls"] == 3
    assert data["ok"] == 1
    assert data["alert"] == 1
    assert data["no_check_yet"] == 1
