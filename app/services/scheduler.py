import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.database import SessionLocal
from app.models import CheckStatus, Snapshot, SnapshotKind, Url, UrlStatus
from app.services.analyzer import Thresholds, analyze
from app.services.fetcher import fetch_and_clean

logger = logging.getLogger(__name__)


def poll_and_check(enqueue_check: Callable[[str], None]) -> None:
    """Find all active URLs whose check interval has elapsed and enqueue checks."""
    now = datetime.now(UTC)
    with SessionLocal() as db:
        rows = db.execute(
            select(Url.id, Url.last_checked_at, Url.frequency)
            .where(Url.status == UrlStatus.active)
        ).all()

        due_ids = [
            row.id for row in rows
            if row.last_checked_at is None
            or now >= row.last_checked_at + timedelta(seconds=row.frequency)
        ]

        if not due_ids:
            logger.debug("No URLs due for checking")
            return

        logger.info("Enqueuing checks for %d URL(s)", len(due_ids))

        for url_id in due_ids:
            enqueue_check(str(url_id))

        db.execute(update(Url).where(Url.id.in_(due_ids)).values(last_checked_at=now))
        db.commit()


def _check_url(url_id: uuid.UUID, notify_alert: Callable[[str], None] | None = None) -> None:
    """Fetch a URL, compare with its baseline snapshot, and persist the result."""
    with SessionLocal() as db:
        url = db.get(Url, url_id)
        if url is None:
            return

        url_str = str(url.url)
        baseline_id = url.current_baseline_id
        embedding_model = url.embedding_model
        llm_model = url.llm_model
        thresholds = Thresholds(
            diff_ok=url.diff_threshold_ok,
            diff_alert=url.diff_threshold_alert,
            cosine_ok=url.cosine_threshold_ok,
            cosine_alert=url.cosine_threshold_alert,
        )
        try:
            html_raw, text_clean = fetch_and_clean(url_str)
        except Exception as exc:
            logger.warning("Fetch failed for %s: %s", url_str, exc)
            db.add(Snapshot(
                url_id=url_id,
                kind=SnapshotKind.check,
                html_raw="",
                text_clean="",
                diff_percentage=0.0,
                similarity_score=None,
                status=CheckStatus.ERROR,
                error_message=str(exc),
            ))
            db.execute(update(Url).where(Url.id == url_id).values(last_checked_at=datetime.now(UTC)))
            db.commit()
            return

        baseline = db.get(Snapshot, baseline_id) if baseline_id else None
        if baseline is None:
            logger.warning("No baseline for %s — skipping", url_str)
            return

        analysis = analyze(
            baseline_text=baseline.text_clean,
            baseline_embedding=baseline.embedding,
            check_text=text_clean,
            thresholds=thresholds,
            embedding_model=embedding_model,
            llm_model=llm_model,
        )
        snapshot = Snapshot(
            url_id=url_id,
            kind=SnapshotKind.check,
            html_raw=html_raw,
            text_clean=text_clean,
            diff_percentage=analysis.diff_percentage,
            similarity_score=analysis.similarity_score,
            status=analysis.status,
            llm_analysis=analysis.llm_analysis,
            embedding=analysis.check_embedding,
        )
        db.add(snapshot)
        db.execute(update(Url).where(Url.id == url_id).values(last_checked_at=datetime.now(UTC)))
        db.commit()

        logger.info("Check for %s → %s (diff=%.1f%%)", url_str, analysis.status, analysis.diff_percentage)

        if analysis.status == CheckStatus.ALERT and notify_alert is not None:
            notify_alert({
                "snapshot_id": str(snapshot.id),
                "url_id": str(url_id),
                "url": url_str,
                "status": analysis.status.value,
                "diff_percentage": analysis.diff_percentage,
                "similarity_score": analysis.similarity_score,
            })
