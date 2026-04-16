"""FastAPI application entry point."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import settings
from app.database import engine
from app.models import Base
from app.routers import baselines, checks, dashboard, urls

logging.basicConfig(level=settings.log_level)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create DB tables on startup."""
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created")
    yield


app = FastAPI(
    title="Web Page Integrity Monitor",
    description="Monitor web pages for defacement using text diff and AI analysis.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(urls.router)
app.include_router(baselines.router)
app.include_router(checks.router)
app.include_router(dashboard.router)


@app.get("/health", tags=["Health"])
def health() -> dict[str, str]:
    """Simple liveness check."""
    return {"status": "ok"}
