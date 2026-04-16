"""Endpoints for check result history."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Snapshot, SnapshotKind
from app.schemas import CheckResultListResponse, CheckResultResponse

router = APIRouter(prefix="/urls", tags=["Checks"])


@router.get("/{url_id}/checks", response_model=CheckResultListResponse)
def list_checks(
    url_id: uuid.UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
) -> CheckResultListResponse:
    """Return paginated check history for a URL, ordered by most recent first."""
    base_filter = (Snapshot.url_id == url_id, Snapshot.kind == SnapshotKind.check)

    total = db.execute(select(func.count()).where(*base_filter)).scalar_one()

    items = list(
        db.execute(
            select(Snapshot)
            .where(*base_filter)
            .order_by(Snapshot.created_at.desc())
            .offset(skip)
            .limit(limit)
        ).scalars().all()
    )
    return CheckResultListResponse(total=total, items=items)

@router.get("/{url_id}/checks/latest", response_model=CheckResultResponse)
def get_latest_check(url_id: uuid.UUID, db: Session = Depends(get_db)) -> Snapshot:
    """Return the most recent check result for a URL."""

    result = db.execute(
        select(Snapshot)
        .where(Snapshot.url_id == url_id, Snapshot.kind == SnapshotKind.check)
        .order_by(Snapshot.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No checks yet for this URL")

    return result
