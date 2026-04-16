"""SQLAlchemy ORM models for the Web Page Integrity Monitor."""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Base class for all ORM models."""


class UrlStatus(StrEnum):
    """Possible states of a monitored URL."""

    active = "active"
    inactive = "inactive"


class CheckStatus(StrEnum):
    """Result status of a periodic check."""

    OK = "OK"
    CHANGED = "CHANGED"
    ALERT = "ALERT"
    ERROR = "ERROR"


class SnapshotKind(StrEnum):
    """Discriminator for the snapshots table."""

    baseline = "baseline"
    check = "check"


class Url(Base):
    """A URL registered for monitoring."""

    __tablename__ = "urls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url: Mapped[str] = mapped_column(String, unique=True, nullable=False)

    # Monitoring frequency in seconds (e.g. 300 = every 5 minutes)
    frequency: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    status: Mapped[UrlStatus] = mapped_column(Enum(UrlStatus), nullable=False, default=UrlStatus.active)

    # AI models used for this URL's analysis pipeline
    embedding_model: Mapped[str] = mapped_column(String, nullable=False, default="text-embedding-3-small")
    llm_model: Mapped[str] = mapped_column(String, nullable=False, default="gpt-4o-mini")

    # Per-URL thresholds for the analysis funnel
    diff_threshold_ok: Mapped[float] = mapped_column(Float, nullable=False, default=5.0)
    diff_threshold_alert: Mapped[float] = mapped_column(Float, nullable=False, default=50.0)
    cosine_threshold_ok: Mapped[float] = mapped_column(Float, nullable=False, default=0.95)
    cosine_threshold_alert: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)

    # Timestamp of the last completed check (used by the scheduler)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    # Pointer to the active baseline snapshot. Updated on refresh (non-destructive).
    # use_alter=True breaks the circular FK cycle between urls and snapshots at DDL time.
    current_baseline_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("snapshots.id", use_alter=True, name="fk_urls_current_baseline"),
        nullable=True,
    )

    # Relationships
    current_baseline: Mapped["Snapshot | None"] = relationship(
        "Snapshot",
        foreign_keys="[Url.current_baseline_id]",
        post_update=True,
    )
    snapshots: Mapped[list["Snapshot"]] = relationship(
        "Snapshot",
        back_populates="url",
        foreign_keys="[Snapshot.url_id]",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        # Speeds up the scheduler query: active URLs with expired last_checked_at
        Index("ix_urls_status_last_checked", "status", "last_checked_at"),
    )


class Snapshot(Base):
    """A point-in-time snapshot of a monitored page.

    Serves as both the baseline reference (kind='baseline') and the periodic
    check record (kind='check').  Baseline snapshots are never overwritten:
    a refresh inserts a new row and moves the Url.current_baseline_id pointer,
    preserving the full history at no extra cost.
    """

    __tablename__ = "snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("urls.id", ondelete="CASCADE"), nullable=False
    )

    # Discriminates between reference snapshots and periodic check snapshots
    kind: Mapped[SnapshotKind] = mapped_column(Enum(SnapshotKind), nullable=False)

    html_raw: Mapped[str] = mapped_column(Text, nullable=False)
    text_clean: Mapped[str] = mapped_column(Text, nullable=False)

    # OpenAI embedding stored as a JSON array of floats
    embedding: Mapped[list[float] | None] = mapped_column(JSON, nullable=True)

    # Populated only when kind='check'
    diff_percentage: Mapped[float | None] = mapped_column(Float, nullable=True)
    similarity_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[CheckStatus | None] = mapped_column(Enum(CheckStatus), nullable=True)
    llm_analysis: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )

    url: Mapped["Url"] = relationship("Url", back_populates="snapshots", foreign_keys="[Snapshot.url_id]")

    __table_args__ = (
        # Covers both "latest check for URL" and "baseline history for URL"
        Index("ix_snapshots_url_kind_created", "url_id", "kind", "created_at"),
    )
