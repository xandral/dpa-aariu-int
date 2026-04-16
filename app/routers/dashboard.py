"""Dashboard endpoints: current state overview and historical event distribution."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import CheckStatus, Snapshot, SnapshotKind, Url
import uuid

from app.schemas import DashboardCurrentResponse, DashboardHistoryResponse, UrlHistoryStats

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/", response_model=DashboardCurrentResponse)
def get_dashboard_current(db: Session = Depends(get_db)) -> DashboardCurrentResponse:
    """Current state: one status vote per URL based on its latest check snapshot.

    Returns the number of URLs in each state (ok/changed/alert/error/no_check_yet).
    Uses a subquery + join to avoid N+1 queries.
    """
    counts: dict[str, int] = {s.value.lower(): 0 for s in CheckStatus}
    total_urls = db.scalar(select(func.count(Url.id))) or 0

    max_created_subq = (
        select(Snapshot.url_id, func.max(Snapshot.created_at).label("max_created"))
        .where(Snapshot.kind == SnapshotKind.check)
        .group_by(Snapshot.url_id)
        .subquery()
    )

    status_counts = db.execute(
        select(Snapshot.status, func.count().label("cnt"))
        .join(
            max_created_subq,
            (Snapshot.url_id == max_created_subq.c.url_id)
            & (Snapshot.created_at == max_created_subq.c.max_created),
        )
        .where(Snapshot.kind == SnapshotKind.check)
        .group_by(Snapshot.status)
    ).all()

    checked = 0
    for check_status, cnt in status_counts:
        counts[check_status.value.lower()] = cnt
        checked += cnt

    return DashboardCurrentResponse(
        total_urls=total_urls,
        ok=counts.get("ok", 0),
        changed=counts.get("changed", 0),
        alert=counts.get("alert", 0),
        error=counts.get("error", 0),
        no_check_yet=total_urls - checked,
    )


@router.get("/history", response_model=DashboardHistoryResponse)
def get_dashboard_history(
    from_dt: datetime = Query(..., description="Start of time range (ISO 8601)"),
    to_dt: datetime = Query(..., description="End of time range (ISO 8601)"),
    url_ids: str | None = Query(None, description="Comma-separated URL UUIDs to filter"),
    db: Session = Depends(get_db),
) -> DashboardHistoryResponse:
    """Event distribution in a time window with per-URL breakdown.

    Both from_dt and to_dt are required (422 if missing).
    Optional url_ids filters to specific URLs (comma-separated UUIDs).
    """
    if from_dt > to_dt:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="from_dt must be before or equal to to_dt",
        )

    filters = [
        Snapshot.kind == SnapshotKind.check,
        Snapshot.created_at >= from_dt,
        Snapshot.created_at <= to_dt,
    ]

    if url_ids:
        parsed_ids = [uuid.UUID(uid.strip()) for uid in url_ids.split(",") if uid.strip()]
        filters.append(Snapshot.url_id.in_(parsed_ids))

    rows = db.execute(
        select(Snapshot.url_id, Snapshot.status, func.count().label("cnt"))
        .where(*filters)
        .group_by(Snapshot.url_id, Snapshot.status)
    ).all()

    url_map: dict[uuid.UUID, dict[str, int]] = {}
    totals: dict[str, int] = {s.value.lower(): 0 for s in CheckStatus}
    grand_total = 0

    for url_id, check_status, cnt in rows:
        key = check_status.value.lower()
        totals[key] = totals.get(key, 0) + cnt
        grand_total += cnt
        if url_id not in url_map:
            url_map[url_id] = {s.value.lower(): 0 for s in CheckStatus}
        url_map[url_id][key] += cnt

    url_names: dict[uuid.UUID, str] = {}
    if url_map:
        for uid, url_str in db.execute(
            select(Url.id, Url.url).where(Url.id.in_(url_map.keys()))
        ).all():
            url_names[uid] = url_str

    url_stats = [
        UrlHistoryStats(
            url_id=uid,
            url=url_names.get(uid, ""),
            total_checks=sum(counts.values()),
            ok=counts.get("ok", 0),
            changed=counts.get("changed", 0),
            alert=counts.get("alert", 0),
            error=counts.get("error", 0),
        )
        for uid, counts in url_map.items()
    ]

    return DashboardHistoryResponse(
        from_dt=from_dt,
        to_dt=to_dt,
        total_checks=grand_total,
        ok=totals.get("ok", 0),
        changed=totals.get("changed", 0),
        alert=totals.get("alert", 0),
        error=totals.get("error", 0),
        urls=url_stats,
    )
