

import logging
import uuid

from app.celery_app import celery_app
from app.services.baseline import acquire_baseline
from app.services.scheduler import _check_url, poll_and_check

logger = logging.getLogger(__name__)


@celery_app.task(
    name="wpim.acquire_baseline",
    ignore_result=False,
    autoretry_for=(Exception,),
    max_retries=3,
    retry_backoff=True,      
    retry_backoff_max=60,
    reject_on_worker_lost=True, # Ensure that if the worker process dies during acquisition, the task is rejected and sent to dead_letters after retries are exhausted.
)
def acquire_baseline_task(url_id: str) -> None:
    """Fetch a page, compute its embedding, and persist a baseline snapshot.

    Retry rationale: acquisition is a one-shot operation triggered by the user.
    A transient OpenAI/network error must not silently leave the URL without a
    baseline.  3 retries with exponential backoff cover most transient failures.
    After all retries are exhausted the message is rejected → dead_letters queue.
    """
    acquire_baseline(uuid.UUID(url_id))


@celery_app.task(name="wpim.run_check")
def run_check_task(url_id: str) -> None:
    """Compare a page against its baseline and persist the check result.

    No explicit retry: checks are periodic.  If one fails, the next
    poll_and_check cycle (≤ scheduler_interval seconds later) re-enqueues
    it automatically.  Adding Celery retries would duplicate tasks and risk
    flooding the queue on systematic failures.
    """
    _check_url(uuid.UUID(url_id), notify_alert=notify_alert_task.delay)


@celery_app.task(name="wpim.poll_and_check")
def poll_and_check_task() -> None:
    """Scan for due URLs and enqueue run_check tasks.  Driven by Celery Beat.

    No retry: if the Beat tick fails, the next scheduled tick handles it.
    The task only dispatches — no state is lost if it crashes mid-run.
    """
    poll_and_check(
        enqueue_check=lambda url_id: run_check_task.apply_async(
            [url_id],
            task_id=f"check:{url_id}:{uuid.uuid4().hex[:8]}",
        )
    )


@celery_app.task(name="wpim.notify_alert")
def notify_alert_task(alert_data: dict) -> None:
    """Send an alert notification for a check snapshot that resulted in ALERT.

    alert_data contains: snapshot_id, url_id, url, status, diff_percentage,
    similarity_score.  An external consumer on the 'notifications' queue could
    read these same fields to dispatch via email / webhook / Slack.
    """
    logger.info(
        "ALERT on %s — diff=%.1f%% (snapshot %s)",
        alert_data.get("url"),
        alert_data.get("diff_percentage", 0),
        alert_data.get("snapshot_id"),
    )
