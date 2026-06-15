"""HTTP client to the model-serving process (worker.server).

Pipelines use this instead of importing torch/models directly, so the arq worker
process holds NO GPU models — only the serving process does (TSA §9). This keeps
VRAM/commit bounded to one process on a single-GPU host.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger("dam.serving_client")
SERVER_URL = os.environ.get("AI_SERVER_URL", "http://127.0.0.1:8100")
# Transient model-server failures to RETRY rather than fail the whole job on. The server
# briefly 503s / refuses connections while it's bouncing or under GPU pressure (the watchdog
# relieves VRAM, an OOM'd model reloads) — recovering in seconds. Without this, ONE such blip
# during a 13-minute video ingest discarded all of it (Restored_Output…mp4 died on a 503 from
# /objects). A bad-input/4xx error is NOT retried — only server-side / connection failures.
_RETRY_STATUS = {502, 503, 504}
_RETRIES = 4
_BACKOFF = 3.0   # seconds, linear: 3, 6, 9, 12
# Generous read timeout: ASR / parsing of large media can take minutes. A scanned PDF's
# VLM-OCR (15 pages × ~60s) runs inside ONE /parse/document call and measured 903s on
# ARUNI.pdf — 900 was exactly too low and failed the whole ingest with "timed out".
_timeout = httpx.Timeout(1800.0, connect=5.0)
# Pooled client: a single video ingest makes dozens of calls (faces/objects/describe per
# keyframe). A new client per call opened+closed a TCP connection each time; this keeps the
# connection alive across calls. The worker is single-job/single-thread, so reuse is safe.
_client = httpx.Client(timeout=_timeout, limits=httpx.Limits(max_keepalive_connections=4))


def _post(path: str, payload: dict) -> dict:
    """POST to the model server, RETRYING transient server/connection failures with backoff so a
    momentary blip doesn't fail the whole asset. Re-raises 4xx (bad input) and the last error."""
    last: Exception | None = None
    for attempt in range(_RETRIES + 1):
        try:
            r = _client.post(f"{SERVER_URL}{path}", json=payload)
            if r.status_code in _RETRY_STATUS:
                r.raise_for_status()       # → HTTPStatusError, caught below for retry
            r.raise_for_status()           # 4xx and other errors: raise immediately (no retry)
            return r.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code not in _RETRY_STATUS or attempt == _RETRIES:
                raise
            last = e
        except (httpx.ConnectError, httpx.ReadError, httpx.RemoteProtocolError,
                httpx.ConnectTimeout, httpx.PoolTimeout) as e:
            if attempt == _RETRIES:
                raise
            last = e
        wait = _BACKOFF * (attempt + 1)
        log.warning("model server %s transient failure (%s); retry %d/%d in %.0fs",
                    path, type(last).__name__, attempt + 1, _RETRIES, wait)
        time.sleep(wait)
    raise last  # unreachable, but keeps the type checker happy


def embed_texts(texts: list[str]) -> list[list[float]]:
    return _post("/embed/texts", {"texts": texts})["vectors"]


def embed_image(storage_uri: str, filename: str) -> list[float]:
    return _post("/embed/image", {"storage_uri": storage_uri, "filename": filename})["vector"]


def embed_image_b64(image_b64: str) -> list[float]:
    return _post("/embed/image-b64", {"image_b64": image_b64})["vector"]


def face_crop(storage_uri: str, filename: str, bbox: list[float], out_key: str) -> str | None:
    """Crop a face avatar (server-side) into out_key. Best-effort — returns None on failure."""
    try:
        return _post("/face-crop", {"storage_uri": storage_uri, "filename": filename,
                                    "bbox": bbox, "out_key": out_key}).get("uri")
    except Exception:
        return None


def transcribe(storage_uri: str, filename: str) -> tuple[list[dict], str | None]:
    out = _post("/asr", {"storage_uri": storage_uri, "filename": filename})
    return out["segments"], out.get("language")


def parse_document(storage_uri: str, filename: str) -> str:
    return _post("/parse/document", {"storage_uri": storage_uri, "filename": filename})["markdown"]


def detect_faces(storage_uri: str, filename: str) -> list[dict]:
    return _post("/faces", {"storage_uri": storage_uri, "filename": filename})["faces"]


def detect_objects(storage_uri: str, filename: str) -> list[dict]:
    return _post("/objects", {"storage_uri": storage_uri, "filename": filename})["objects"]


def caption(storage_uri: str, filename: str) -> str:
    return _post("/caption", {"storage_uri": storage_uri, "filename": filename}).get("caption", "")


def ocr(storage_uri: str, filename: str) -> str:
    """Dedicated text extraction (verbatim on-image text), separate from the caption."""
    return _post("/ocr", {"storage_uri": storage_uri, "filename": filename}).get("text", "")


def describe(storage_uri: str, filename: str, max_side: int | None = None) -> dict:
    """Structured scene fields + on-image text in ONE VLM pass:
    {caption, text, people, objects[], actions[], intent}.
    max_side=512 for video keyframes (≈2× faster VLM); None → server default 1024."""
    out = _post("/describe", {"storage_uri": storage_uri, "filename": filename,
                              "max_side": max_side})
    return {"caption": out.get("caption", ""), "text": out.get("text", ""),
            "people": out.get("people", ""), "objects": out.get("objects", []),
            "actions": out.get("actions", []), "intent": out.get("intent", "")}


def summarize(notes: str, kind: str = "video") -> str:
    """One-sentence asset summary from per-shot tags / extracted text."""
    try:
        return _post("/summarize", {"notes": notes, "kind": kind}).get("summary", "")
    except Exception:
        return ""
