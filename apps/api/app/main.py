"""DAM Platform API — application plane entrypoint.

Run (dev):  uvicorn app.main:app --reload --port 8000
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from .config import settings
from . import bootstrap
from .routers import (
    admin,
    assets,
    auth,
    collections,
    distribution,
    health,
    persons,
    search,
    stats,
    workflow,
)

logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bootstrap.run()
    yield


app = FastAPI(
    title="AI DAM Platform API",
    version="0.1.0",
    description="Unified intelligent search across documents, images, audio & video.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(DBAPIError)
async def _dbapi_error_handler(request: Request, exc: DBAPIError):
    """A malformed path value (most often a non-UUID asset id) fails at asyncpg
    parameter-encoding and surfaces as a generic DBAPIError. Treat the UUID/data
    case as a 422 (bad client input); anything else is a genuine 500. The request
    session is rolled back by get_db's context-manager exit."""
    msg = str(getattr(exc, "orig", None) or exc).lower()
    if "uuid" in msg or "invalid input" in msg:
        return JSONResponse(status_code=422, content={"detail": "Malformed identifier or value."})
    logging.getLogger("dam.api").exception("database error", exc_info=exc)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})

app.include_router(health.router, prefix="/api")
app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(search.router)
app.include_router(collections.router)
app.include_router(distribution.router)
app.include_router(workflow.router)
app.include_router(persons.router)
app.include_router(stats.router)
app.include_router(admin.router)


@app.get("/")
async def root():
    return {"service": "dam-api", "version": "0.1.0", "docs": "/docs"}
