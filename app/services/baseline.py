"""Baseline acquisition service."""

import logging
import uuid

from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Snapshot, SnapshotKind, Url
from app.services.analyzer import compute_embedding
from app.services.fetcher import fetch_and_clean

logger = logging.getLogger(__name__)


def _acquire_baseline(url_id: uuid.UUID, session: Session) -> None:
    """Acquire a baseline using a caller-provided session. Does NOT commit."""
    url = session.get(Url, url_id)
    if url is None:
        logger.warning("acquire_baseline: URL %s not found", url_id)
        return
    if url.current_baseline_id is not None:
        logger.info("acquire_baseline: URL %s already has a baseline, skipping", url_id)
        return

    url_str = str(url.url)
    html_raw, text_clean = fetch_and_clean(url_str)
    embedding = compute_embedding(text_clean, url.embedding_model)

    snapshot = Snapshot(
        url_id=url_id,
        kind=SnapshotKind.baseline,
        html_raw=html_raw,
        text_clean=text_clean,
        embedding=embedding,
    )
    session.add(snapshot)
    session.flush()  # get snapshot.id before setting the pointer
    url.current_baseline_id = snapshot.id
    logger.info("Baseline acquired for %s", url_str)


def acquire_baseline(url_id: uuid.UUID) -> None:
    """Acquire a baseline in a fresh session (Celery task entry point)."""
    with SessionLocal() as session:
        try:
            _acquire_baseline(url_id, session)
            session.commit()
        except Exception as exc:
            logger.error("acquire_baseline failed for %s: %s", url_id, exc)
            session.rollback()
            raise  # Re-raise so Celery stores the FAILURE state and traceback
