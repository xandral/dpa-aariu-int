"""Endpoints for baseline management."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Snapshot, SnapshotKind, Url
from app.schemas import BaselineResponse
from app.services.analyzer import compute_embedding
from app.services.fetcher import fetch_and_clean

router = APIRouter(prefix="/urls", tags=["Baselines"])


@router.get("/{url_id}/baseline", response_model=BaselineResponse)
def get_baseline(url_id: uuid.UUID, db: Session = Depends(get_db)) -> Snapshot:
    """Return the current baseline snapshot for a URL."""
    baseline = db.execute(
        select(Snapshot)
        .join(Url, Url.current_baseline_id == Snapshot.id)
        .where(Url.id == url_id)
    ).scalar_one_or_none()

    if baseline is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Baseline not found")
    return baseline


@router.post("/{url_id}/baseline/refresh", response_model=BaselineResponse)
def refresh_baseline(url_id: uuid.UUID, db: Session = Depends(get_db)) -> Snapshot:
    """Re-fetch the page and create a new baseline snapshot (non-destructive)."""
    url_obj = db.execute(select(Url).where(Url.id == url_id)).scalar_one_or_none()
    if not url_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found")

    try:
        html_raw, text_clean = fetch_and_clean(str(url_obj.url))
        embedding = compute_embedding(text_clean, url_obj.embedding_model)
    except Exception as exc: 
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not fetch the URL: {exc}",
        ) from exc

    new_baseline = Snapshot(
        url_id=url_id,
        kind=SnapshotKind.baseline,
        html_raw=html_raw,
        text_clean=text_clean,
        embedding=embedding,
    )
    db.add(new_baseline)
    db.flush()

    url_obj.current_baseline_id = new_baseline.id
    db.commit()
    return new_baseline
