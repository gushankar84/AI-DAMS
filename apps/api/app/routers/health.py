"""Liveness + dependency health (NFR-A: system health & job status)."""
from fastapi import APIRouter
from sqlalchemy import text

from ..db import engine
from ..search import opensearch_store, qdrant_store
from ..search import embed_client

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    return {"status": "ok"}


@router.get("/health/deps")
async def deps():
    """Probe each backing store + the model-serving worker."""
    out: dict[str, str] = {}
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        out["postgres"] = "ok"
    except Exception as e:
        out["postgres"] = f"error: {e}"
    try:
        qdrant_store.client().get_collections()
        out["qdrant"] = "ok"
    except Exception as e:
        out["qdrant"] = f"error: {e}"
    try:
        opensearch_store.client().cluster.health()
        out["opensearch"] = "ok"
    except Exception as e:
        out["opensearch"] = f"error: {e}"
    out["ai_worker"] = "ok" if embed_client.embed_text("ping") else "unreachable (keyword-only search)"
    return out
