"""Pydantic schemas for request validation and response serialization."""

import uuid
from datetime import datetime

from pydantic import AnyHttpUrl, BaseModel, Field, field_validator

from app.config import settings
from app.models import CheckStatus, UrlStatus

# ---------------------------------------------------------------------------
# URL schemas
# ---------------------------------------------------------------------------


class UrlCreate(BaseModel):
    """Payload to register a new URL for monitoring."""

    url: AnyHttpUrl
    frequency: int = Field(
        default_factory=lambda: settings.default_frequency, gt=0, description="Check interval in seconds"
    )

    # AI models — fall back to global defaults if not provided
    embedding_model: str = Field(default_factory=lambda: settings.embedding_model)
    llm_model: str = Field(default_factory=lambda: settings.llm_model)

    # Analysis thresholds — fall back to global defaults if not provided
    diff_threshold_ok: float = Field(default_factory=lambda: settings.default_diff_threshold_ok, ge=0.0, le=100.0)
    diff_threshold_alert: float = Field(default_factory=lambda: settings.default_diff_threshold_alert, ge=0.0, le=100.0)
    cosine_threshold_ok: float = Field(default_factory=lambda: settings.default_cosine_threshold_ok, ge=0.0, le=1.0)
    cosine_threshold_alert: float = Field(
        default_factory=lambda: settings.default_cosine_threshold_alert, ge=0.0, le=1.0
    )

    @field_validator("url", mode="before")
    @classmethod
    def normalise_url(cls, v: object) -> object:
        """Strip trailing slash for consistent deduplication."""
        if isinstance(v, str):
            return v.rstrip("/")
        return v


class UrlUpdate(BaseModel):
    """Payload to update an existing URL. All fields are optional."""

    frequency: int | None = Field(default=None, gt=0)
    status: UrlStatus | None = None
    embedding_model: str | None = None
    llm_model: str | None = None
    diff_threshold_ok: float | None = Field(default=None, ge=0.0, le=100.0)
    diff_threshold_alert: float | None = Field(default=None, ge=0.0, le=100.0)
    cosine_threshold_ok: float | None = Field(default=None, ge=0.0, le=1.0)
    cosine_threshold_alert: float | None = Field(default=None, ge=0.0, le=1.0)


class UrlCreateResponse(BaseModel):
    """Response for POST /urls/ — just the id, baseline acquisition is async."""

    id: uuid.UUID

    model_config = {"from_attributes": True}


class UrlSummaryResponse(BaseModel):
    """Lightweight URL representation used in list responses."""

    id: uuid.UUID
    url: str
    status: UrlStatus
    last_checked_at: datetime | None

    model_config = {"from_attributes": True}


class UrlResponse(BaseModel):
    """Full representation of a monitored URL."""

    id: uuid.UUID
    url: str
    frequency: int
    status: UrlStatus
    embedding_model: str
    llm_model: str
    diff_threshold_ok: float
    diff_threshold_alert: float
    cosine_threshold_ok: float
    cosine_threshold_alert: float
    last_checked_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Baseline schemas
# ---------------------------------------------------------------------------


class BaselineResponse(BaseModel):
    """Current baseline snapshot for a URL (html_raw excluded to keep responses light).

    Snapshots are immutable: a refresh creates a new row rather than updating
    this one, so there is no updated_at field.
    """

    id: uuid.UUID
    url_id: uuid.UUID
    text_clean: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BaselineStatusResponse(BaseModel):
    """Acquisition state of the baseline for a URL.

    Sourced from celery_taskmeta (Celery result backend) when the baseline
    is not yet ready; shortcut to SUCCESS once current_baseline_id is set.

    state values mirror Celery task states: PENDING | STARTED | SUCCESS | FAILURE | RETRY.
    """

    state: str
    ready: bool
    error: str | None = None


# ---------------------------------------------------------------------------
# Check result schemas
# ---------------------------------------------------------------------------


class CheckResultResponse(BaseModel):
    """Result of a single periodic check."""

    id: uuid.UUID
    url_id: uuid.UUID
    diff_percentage: float
    similarity_score: float | None
    status: CheckStatus
    llm_analysis: dict | None
    error_message: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class CheckResultListResponse(BaseModel):
    """Paginated list of check results."""

    total: int
    items: list[CheckResultResponse]


# ---------------------------------------------------------------------------
# Dashboard schemas
# ---------------------------------------------------------------------------


class DashboardCurrentResponse(BaseModel):
    """Current state: one status vote per URL based on its latest check snapshot."""

    total_urls: int
    ok: int
    changed: int
    alert: int
    error: int
    no_check_yet: int


class UrlHistoryStats(BaseModel):
    """Check stats for a single URL in a time window."""

    url_id: uuid.UUID
    url: str
    total_checks: int
    ok: int
    changed: int
    alert: int
    error: int


class DashboardHistoryResponse(BaseModel):
    """Event distribution in a time window, with optional per-URL breakdown."""

    from_dt: datetime
    to_dt: datetime
    total_checks: int
    ok: int
    changed: int
    alert: int
    error: int
    urls: list[UrlHistoryStats]
