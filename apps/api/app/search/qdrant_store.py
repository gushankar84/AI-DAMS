"""Qdrant access — vector candidate generation.

Holds dense text, image, and face vectors (multi-vector ColQwen page embeddings
are added in P1-advanced). The API only *reads* here; the ai-worker writes points
during ingestion.
"""
from functools import lru_cache

from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from ..config import settings
from . import constants as C


@lru_cache
def client() -> QdrantClient:
    return QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=15)


def ensure_collections() -> None:
    """Idempotently create the dense collections. Safe to call on startup."""
    c = client()
    specs = {
        C.QDRANT_TEXT: C.DIM_TEXT,
        C.QDRANT_IMAGE: C.DIM_IMAGE,
        C.QDRANT_FACE: C.DIM_FACE,
    }
    existing = {col.name for col in c.get_collections().collections}
    for name, dim in specs.items():
        if name not in existing:
            c.create_collection(
                collection_name=name,
                vectors_config=qm.VectorParams(size=dim, distance=qm.Distance.COSINE),
            )
            # payload indexes for fast filtering by asset attributes
            for field in ("asset_id", "asset_type", "department", "project"):
                try:
                    c.create_payload_index(name, field, qm.PayloadSchemaType.KEYWORD)
                except Exception:
                    pass


def _filter(types: list[str] | None, department: str | None, project: str | None):
    must = []
    if types:
        must.append(qm.FieldCondition(key="asset_type", match=qm.MatchAny(any=types)))
    if department:
        must.append(qm.FieldCondition(key="department", match=qm.MatchValue(value=department)))
    if project:
        must.append(qm.FieldCondition(key="project", match=qm.MatchValue(value=project)))
    return qm.Filter(must=must) if must else None


def search(
    collection: str,
    vector: list[float],
    limit: int = 50,
    types: list[str] | None = None,
    department: str | None = None,
    project: str | None = None,
    score_threshold: float | None = None,
) -> list[dict]:
    """Return [{asset_id, score, payload}] ranked by cosine similarity.

    `score_threshold` drops the nearest-neighbour noise floor (Qdrant filters
    server-side), so only genuinely-similar hits are returned.
    """
    c = client()
    res = c.query_points(
        collection_name=collection,
        query=vector,
        limit=limit,
        with_payload=True,
        score_threshold=score_threshold,
        query_filter=_filter(types, department, project),
    ).points
    return [
        {"asset_id": p.payload.get("asset_id"), "score": float(p.score), "payload": p.payload}
        for p in res
        if p.payload and p.payload.get("asset_id")
    ]
