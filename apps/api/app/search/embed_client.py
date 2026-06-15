"""Thin client to the ai-worker inference endpoint for query-time embeddings.

The API process deliberately holds no ML models. Query vectors come from the
ai-worker (the model-serving plane — vLLM/TEI in P6). If the worker is
unreachable, callers degrade gracefully to keyword-only search.
"""
import os

import httpx

# NOTE: 127.0.0.1, NOT localhost. The model server binds IPv4 only; on Windows
# "localhost" resolves to ::1 (IPv6) first and the failed attempt adds ~2.4s PER
# call before falling back to IPv4. A persistent (pooled) client also avoids
# per-call TCP/handshake setup. Together this took query embedding 2400ms -> ~10ms.
WORKER_URL = os.environ.get("AI_WORKER_URL", "http://127.0.0.1:8100").replace("localhost", "127.0.0.1")
_timeout = httpx.Timeout(20.0, connect=3.0)
_client = httpx.Client(timeout=_timeout)


def _post(path: str, payload: dict) -> dict | None:
    try:
        r = _client.post(f"{WORKER_URL}{path}", json=payload)
        r.raise_for_status()
        return r.json()
    except Exception:
        return None


def embed_text(text: str) -> list[float] | None:
    """Dense text embedding (BGE-M3) for semantic text/transcript search."""
    out = _post("/embed/text", {"text": text})
    return out.get("vector") if out else None


def embed_clip_text(text: str) -> list[float] | None:
    """CLIP text encoder — lands in the shared image space for text->image search."""
    out = _post("/embed/clip-text", {"text": text})
    return out.get("vector") if out else None


def detect_faces(storage_uri: str, filename: str) -> list[dict] | None:
    """Detect faces + ArcFace embeddings in a (query) image via the model server."""
    out = _post("/faces", {"storage_uri": storage_uri, "filename": filename})
    return out.get("faces") if out else None


def rerank(query: str, passages: list[str]) -> list[float] | None:
    """Cross-encoder rerank scores for (query, passage) pairs (P4 precision stage)."""
    if not passages:
        return []
    out = _post("/rerank", {"query": query, "passages": passages})
    return out.get("scores") if out else None


# LLM judging can take a few seconds (and the model may cold-start), so it gets its own
# generous client. Used only on the top-K of noise-prone (long) queries — see search.py.
_llm_client = httpx.Client(timeout=httpx.Timeout(60.0, connect=3.0))


def llm_filter(query: str, items: list[str]) -> list[int] | None:
    """LLM relevance judge over candidate texts (multilingual, by meaning). Returns the
    indices of items that genuinely match the query, or None on failure — in which case
    the caller keeps all (an LLM outage must never silently empty the results)."""
    if not items:
        return []
    try:
        r = _llm_client.post(f"{WORKER_URL}/llm-filter", json={"query": query, "items": items})
        r.raise_for_status()
        return r.json().get("relevant")
    except Exception:
        return None
