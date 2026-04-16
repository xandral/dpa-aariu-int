"""CRUD endpoints for monitored URLs."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Url
from app.schemas import UrlCreate, UrlCreateResponse, UrlResponse, UrlSummaryResponse, UrlUpdate
from app.tasks import acquire_baseline_task

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/urls", tags=["URLs"])


@router.post("/", response_model=UrlCreateResponse, status_code=status.HTTP_202_ACCEPTED)
def create_url(payload: UrlCreate, db: Session = Depends(get_db)) -> Url:
    """Register a new URL and enqueue an async task to acquire its baseline."""
    existing = db.execute(select(Url).where(Url.url == str(payload.url))).scalar_one_or_none()
    if existing:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="URL already registered")

    url_obj = Url(
        url=str(payload.url),
        frequency=payload.frequency,
        embedding_model=payload.embedding_model,
        llm_model=payload.llm_model,
        diff_threshold_ok=payload.diff_threshold_ok,
        diff_threshold_alert=payload.diff_threshold_alert,
        cosine_threshold_ok=payload.cosine_threshold_ok,
        cosine_threshold_alert=payload.cosine_threshold_alert,
    )
    db.add(url_obj)
    db.commit()

    acquire_baseline_task.apply_async(
        [str(url_obj.id)],
        task_id=f"baseline:{url_obj.id}", # not necessary?
    )
    return url_obj


@router.get("/", response_model=list[UrlSummaryResponse])
def list_urls(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> list[Url]:
    """List all registered URLs with pagination (lightweight summary)."""
    return list(db.execute(select(Url).offset(skip).limit(limit)).scalars().all())


@router.get("/{url_id}", response_model=UrlResponse)
def get_url(url_id: uuid.UUID, db: Session = Depends(get_db)) -> Url:
    """Get details of a single monitored URL."""
    url_obj = db.execute(select(Url).where(Url.id == url_id)).scalar_one_or_none()
    if not url_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found")
    return url_obj


@router.put("/{url_id}", response_model=UrlResponse)
def update_url(url_id: uuid.UUID, payload: UrlUpdate, db: Session = Depends(get_db)) -> Url:
    """Update frequency, status, or thresholds of a monitored URL."""
    url_obj = db.execute(select(Url).where(Url.id == url_id)).scalar_one_or_none()
    if not url_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found")

    for field, value in payload.model_dump(exclude_none=True).items():
        setattr(url_obj, field, value)

    db.commit()
    db.refresh(url_obj)
    return url_obj


@router.delete("/{url_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_url(url_id: uuid.UUID, db: Session = Depends(get_db)) -> None:
    """Delete a URL and all associated snapshots."""
    url_obj = db.execute(select(Url).where(Url.id == url_id)).scalar_one_or_none()
    if not url_obj:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="URL not found")

    db.delete(url_obj)
    db.commit()
