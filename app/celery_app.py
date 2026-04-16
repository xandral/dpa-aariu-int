from celery import Celery
from kombu import Exchange, Queue

from app.config import settings

# ---------------------------------------------------------------------------
# Queue topology
#
#   default       ──► normal task execution
#   notifications ──► alert notifications (wpim.notify_alert)
#   dead_letters  ──► receives messages from default/notifications when:
#                     • the worker process crashes mid-task (reject_on_worker_lost)
#                     • a task is nacked after exhausting all retries
#
# Both working queues declare x-dead-letter-exchange=dead_letters so RabbitMQ
# automatically routes dead messages there without any application-level logic.
# ---------------------------------------------------------------------------



_QUEUES = (
    Queue(
        "default",
        Exchange("default", type="direct"),
        routing_key="default",
        durable=True,
        queue_arguments={"x-dead-letter-exchange": "dead_letters"},
    ),
    Queue(
        "notifications",
        Exchange("notifications", type="direct"),
        routing_key="notifications",
        durable=True,
        queue_arguments={"x-dead-letter-exchange": "dead_letters"},
    ),
    Queue(
        "dead_letters",
        Exchange("dead_letters", type="fanout", durable=True),
       # routing_key="#",
        durable=True,
    ),
)

celery_app = Celery(
    "wpim",
    broker=settings.rabbitmq_url,
    result_backend="db+" + settings.database_url,
    include=["app.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
  #  task_ignore_result=True,
    broker_connection_retry_on_startup=True,
    # Queue topology
    task_queues=_QUEUES,
    task_default_queue="default",
    # Acknowledge only after task completes so crashes trigger dead-lettering.
    task_acks_late=True,
    # Reject (not requeue) if the worker process dies — routes to dead_letters.
    task_reject_on_worker_lost=True,
    task_routes={
        "wpim.notify_alert": {"queue": "notifications"},
    },
    beat_schedule={
        "poll-urls": {
            "task": "wpim.poll_and_check",
            "schedule": settings.scheduler_interval,
        },
    },
)
