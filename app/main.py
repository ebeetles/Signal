"""FastAPI app: routes, CORS, error handling."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import db, errors
from app.config import get_settings


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
