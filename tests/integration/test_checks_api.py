"""Integration tests for check result endpoints."""

import uuid

from sqlalchemy.orm import Session
from starlette.testclient import TestClient

from app.models import CheckStatus, Snapshot, SnapshotKind


def _insert_check(db: Session, url_id: uuid.UUID, status: CheckStatus) -> Snapshot:
    """Helper to insert a fake check snapshot directly into the DB."""
    result = Snapshot(
        url_id=url_id,
        kind=SnapshotKind.check,
        html_raw="<html></html>",
        text_clean="some text",
        diff_percentage=10.0,
        similarity_score=0.9,
        status=status,
        llm_analysis=None,
    )
    db.add(result)
    db.commit()
    return result


def test_list_checks_empty(client: TestClient, mock_fetch_and_clean, mock_compute_embedding):
    create_resp = client.post("/urls/", json={"url": "https://example.com"})
    url_id = create_resp.json()["id"]

    response = client.get(f"/urls/{url_id}/checks")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []


def test_list_checks(
    client: TestClient, db_session: Session, mock_fetch_and_clean, mock_compute_embedding
):
    create_resp = client.post("/urls/", json={"url": "https://example.com"})
    url_id = uuid.UUID(create_resp.json()["id"])

    _insert_check(db_session, url_id, CheckStatus.OK)
    _insert_check(db_session, url_id, CheckStatus.ALERT)

    response = client.get(f"/urls/{url_id}/checks")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2


def test_get_latest_check(
    client: TestClient, db_session: Session, mock_fetch_and_clean, mock_compute_embedding
):
    create_resp = client.post("/urls/", json={"url": "https://example.com"})
    url_id = uuid.UUID(create_resp.json()["id"])

    _insert_check(db_session, url_id, CheckStatus.OK)
    _insert_check(db_session, url_id, CheckStatus.ALERT)

    response = client.get(f"/urls/{url_id}/checks/latest")
    assert response.status_code == 200
    assert response.json()["status"] == "ALERT"


def test_get_latest_check_no_checks(client: TestClient, mock_fetch_and_clean, mock_compute_embedding):
    create_resp = client.post("/urls/", json={"url": "https://example.com"})
    url_id = create_resp.json()["id"]

    response = client.get(f"/urls/{url_id}/checks/latest")
    assert response.status_code == 404


def test_list_checks_unknown_url_returns_empty(client: TestClient):
    response = client.get(f"/urls/{uuid.uuid4()}/checks")
    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["items"] == []
