"""FastAPI app: routes, CORS, error handling."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import BackgroundTasks, Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db, errors
from app.auth import require_cron_token
from app.collect import run_collect
from app.config import get_settings
from app.schemas import Accepted


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    _setup_logging()
    yield
    await db.close_pool()


app = FastAPI(
    title="Signal",
    version="1.0.0",
    description="Personal morning brief: convergence-ranked tech items, public archive of approved picks.",
    lifespan=lifespan,
)
errors.install(app)

_origin = get_settings().portfolio_origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=[_origin] if _origin else [],
    allow_methods=["GET"],
    allow_headers=[],
    allow_credentials=False,
    max_age=600,
)


@app.get("/health", tags=["ops"])
async def health() -> dict:
    """Keep-warm target. Never touches the database."""
    return {"status": "ok"}


@app.post(
    "/collect",
    tags=["ops"],
    status_code=202,
    response_model=Accepted,
    dependencies=[Depends(require_cron_token)],
    summary="Start an hourly collect run (returns immediately)",
)
async def collect(background: BackgroundTasks) -> dict:
    background.add_task(run_collect)
    return {"status": "accepted", "job": "collect"}
