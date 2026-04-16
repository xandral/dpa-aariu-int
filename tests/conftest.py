"""Shared pytest fixtures for unit and integration tests."""

# Set test environment variables BEFORE importing any app module.
# app/database.py creates the engine at module level using settings.database_url,
# so DATABASE_URL must point to SQLite before the first import.
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("AI_API_KEY", "test-key")
os.environ.setdefault("RABBITMQ_URL", "amqp://guest:guest@localhost:5672/")

import uuid
from collections.abc import Generator
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient

from app.database import get_db
from app.main import app
from app.models import Base

TEST_DATABASE_URL = "sqlite:///:memory:"


@pytest.fixture()
def db_engine():
    """In-memory SQLite engine shared across all sessions via StaticPool."""
    engine = create_engine(
        TEST_DATABASE_URL,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)

    # Patch the app-level engine so the FastAPI lifespan create_all
    # targets the same test database instead of PostgreSQL.
    with patch("app.database.engine", new=engine):
        yield engine

    engine.dispose()


@pytest.fixture()
def db_session(db_engine) -> Generator[Session, None, None]:
    """Session against the test database."""
    session = sessionmaker(bind=db_engine)()
    yield session
    session.close()


@pytest.fixture()
def client(db_engine) -> Generator[TestClient, None, None]:
    """Starlette TestClient wired to the FastAPI app with a test database.

    ``acquire_baseline_task.delay()`` is replaced with a fake that runs
    baseline acquisition inline so tests don't need RabbitMQ and the
    baseline is ready before the POST response returns.
    """
    factory = sessionmaker(bind=db_engine)

    def override_get_db() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    class _FakeAcquireBaselineTask:
        def delay(self, url_id_str: str) -> None:
            from app.services.baseline import _acquire_baseline

            session = factory()
            try:
                _acquire_baseline(uuid.UUID(url_id_str), session)
                session.commit()
            finally:
                session.close()

        def apply_async(self, args: list, **_kwargs) -> None:
            self.delay(*args)

    with patch("app.routers.urls.acquire_baseline_task", new=_FakeAcquireBaselineTask()):
        with TestClient(app=app) as tc:
            yield tc

    app.dependency_overrides.clear()


@pytest.fixture()
def mock_fetch_and_clean():
    """Patch fetcher so tests do not make real HTTP requests."""
    with patch("app.services.baseline.fetch_and_clean") as mock:
        mock.return_value = ("<html><body>Hello world</body></html>", "Hello world")
        yield mock


@pytest.fixture()
def mock_compute_embedding():
    """Patch embedding so tests do not call OpenAI."""
    with patch("app.services.baseline.compute_embedding") as mock:
        mock.return_value = [0.1] * 1536
        yield mock


@pytest.fixture()
def mock_baseline_refresh_embedding():
    """Patch embedding for baseline refresh endpoint."""
    with patch("app.routers.baselines.compute_embedding") as mock:
        mock.return_value = [0.1] * 1536
        yield mock


@pytest.fixture()
def mock_baseline_refresh_fetch():
    """Patch fetcher for baseline refresh endpoint."""
    with patch("app.routers.baselines.fetch_and_clean") as mock:
        mock.return_value = ("<html><body>Updated content</body></html>", "Updated content")
        yield mock
