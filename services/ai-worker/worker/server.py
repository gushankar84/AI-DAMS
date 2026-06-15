"""Model-serving plane (TSA §9) — the SINGLE process that holds GPU models.

Both the API (query-time embeddings) and the ingestion worker (HTTP, no local
models) call these endpoints. Heavy models (Whisper; P3 VLM) are released after
use so VRAM/commit stays bounded on a single-GPU host. In P6 this is swapped for
vLLM + Text-Embeddings-Inference behind the same routes.

Run:  uvicorn worker.server:app --port 8100
"""
from __future__ import annotations

import logging
import os
import tempfile

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from . import asr, caption, models, parsing, stores, vision

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dam.server")
app = FastAPI(title="DAM AI Worker — model serving", version="0.2.0")


class TextIn(BaseModel):
    text: str


class TextsIn(BaseModel):
    texts: list[str]


class AssetRef(BaseModel):
    storage_uri: str
    filename: str = "asset"
    # /describe only: VLM input cap. Video keyframes pass 512 (≈2× faster, tags intact);
    # images default 1024 (OCR of small text needs the resolution). Other endpoints ignore it.
    max_side: int | None = None


class VectorOut(BaseModel):
    vector: list[float]
    dim: int


def _download(ref: AssetRef, tmp: str) -> str:
    dest = os.path.join(tmp, ref.filename)
    stores.download_to(ref.storage_uri, dest)
    return dest


@app.get("/health")
async def health():
    return {"status": "ok", "capabilities": models.capabilities()}


# ─── Text / transcript embeddings ──────────────────────────────────────────
@app.post("/embed/text", response_model=VectorOut)
async def embed_text(body: TextIn):
    try:
        v = models.embed_text(body.text)
        return VectorOut(vector=v, dim=len(v))
    except Exception as e:
        raise HTTPException(503, f"text embedder unavailable: {e}")


@app.post("/embed/texts")
async def embed_texts(body: TextsIn):
    try:
        return {"vectors": models.embed_texts(body.texts)}
    except Exception as e:
        raise HTTPException(503, f"text embedder unavailable: {e}")


class RerankIn(BaseModel):
    query: str
    passages: list[str]


@app.post("/rerank")
async def rerank(body: RerankIn):
    try:
        return {"scores": models.rerank(body.query, body.passages)}
    except Exception as e:
        raise HTTPException(503, f"reranker unavailable: {e}")


# ─── CLIP (image + text shared space) ──────────────────────────────────────
@app.post("/embed/clip-text", response_model=VectorOut)
async def embed_clip_text(body: TextIn):
    try:
        v = models.embed_clip_text(body.text)
        return VectorOut(vector=v, dim=len(v))
    except Exception as e:
        raise HTTPException(503, f"clip text encoder unavailable: {e}")


@app.post("/embed/image", response_model=VectorOut)
async def embed_image(ref: AssetRef):
    try:
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path = _download(ref, tmp)
            with Image.open(path) as im:
                v = models.embed_image(im)
        return VectorOut(vector=v, dim=len(v))
    except Exception as e:
        raise HTTPException(503, f"image embedder unavailable: {e}")


class FaceCropIn(BaseModel):
    storage_uri: str
    filename: str
    bbox: list[float]   # [x1,y1,x2,y2] in the SOURCE image's pixels
    out_key: str        # S3 key to write the crop to


@app.post("/face-crop")
def face_crop(body: FaceCropIn):
    """Crop a face (padded) from its source image/keyframe and store it as a small
    avatar so the People UI can show who each cluster is. Idempotent on out_key.

    NOTE: sync `def` on purpose — FastAPI runs it in a threadpool so a burst of crop
    requests (e.g. opening the People tab) doesn't block the event loop and stall
    concurrent search embed/rerank calls."""
    try:
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            path = _download(AssetRef(storage_uri=body.storage_uri, filename=body.filename), tmp)
            with Image.open(path) as im:
                im = im.convert("RGB")
                w, h = im.size
                x1, y1, x2, y2 = body.bbox
                px, py = (x2 - x1) * 0.35, (y2 - y1) * 0.35  # pad for context
                crop = im.crop((max(0, int(x1 - px)), max(0, int(y1 - py)),
                                min(w, int(x2 + px)), min(h, int(y2 + py))))
                crop.thumbnail((256, 256))
                dst = os.path.join(tmp, "face.jpg")
                crop.save(dst, "JPEG", quality=85)
            uri = stores.upload_file(dst, body.out_key, "image/jpeg")
        return {"uri": uri}
    except Exception as e:
        raise HTTPException(503, f"face crop failed: {e}")


class ImageB64(BaseModel):
    image_b64: str


@app.post("/embed/image-b64", response_model=VectorOut)
async def embed_image_b64(body: ImageB64):
    """Embed raw image bytes (used for region crops — a person/face cut out of a
    larger image so fine details aren't diluted by the whole-frame embedding)."""
    try:
        import base64
        import io

        from PIL import Image
        with Image.open(io.BytesIO(base64.b64decode(body.image_b64))) as im:   # was leaked
            v = models.embed_image(im)
        return VectorOut(vector=v, dim=len(v))
    except Exception as e:
        raise HTTPException(503, f"image embedder unavailable: {e}")


# ─── ASR ───────────────────────────────────────────────────────────────────
@app.post("/asr")
async def transcribe(ref: AssetRef):
    try:
        # CO-RESIDENCY: the VLM is NOT evicted anymore. Whisper at int8 (~2GB) fits beside the
        # 7GB VLM on the 20GB GPU, so the old evict→load→cold-reload dance (60-90s/video, crash
        # window when eviction lagged) is gone. asr._load() has its own VRAM guard and falls
        # back to CPU when the user's own GPU jobs have taken the headroom.
        with tempfile.TemporaryDirectory() as tmp:
            path = _download(ref, tmp)
            segments, language = asr.transcribe(path)
        asr.release()  # free Whisper VRAM/commit after use (keeps headroom for external jobs)
        return {"segments": segments, "language": language}
    except Exception as e:
        asr.release()
        raise HTTPException(503, f"ASR unavailable: {e}")


# ─── Document parsing ──────────────────────────────────────────────────────
@app.post("/parse/document")
async def parse_document(ref: AssetRef):
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = _download(ref, tmp)
            md = parsing.parse_to_markdown(path)
        return {"markdown": md}
    except Exception as e:
        raise HTTPException(503, f"parser unavailable: {e}")


# ─── Vision: faces, objects, caption ───────────────────────────────────────
class ImagePath(BaseModel):
    """For an already-local frame (server-side temp), used by the video pipeline."""
    path: str


@app.post("/faces")
async def faces(ref: AssetRef):
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = _download(ref, tmp)
            return {"faces": vision.detect_faces(path)}
    except Exception as e:
        raise HTTPException(503, f"face model unavailable: {e}")


@app.post("/objects")
async def objects(ref: AssetRef):
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = _download(ref, tmp)
            return {"objects": vision.detect_objects(path)}
    except Exception as e:
        raise HTTPException(503, f"object model unavailable: {e}")


@app.post("/caption")
async def caption_image(ref: AssetRef):
    """Scene/activity caption (optional; returns '' if no backend configured)."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = _download(ref, tmp)
            return {"caption": caption.caption(path)}
    except Exception as e:
        log.warning("caption failed: %s", e)
        return {"caption": ""}


@app.post("/ocr")
async def ocr_image(ref: AssetRef):
    """Dedicated multilingual text extraction (returns '' if no backend / no text)."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = _download(ref, tmp)
            return {"text": caption.ocr(path)}
    except Exception as e:
        log.warning("ocr failed: %s", e)
        return {"text": ""}


@app.post("/describe")
async def describe_image(ref: AssetRef):
    """Structured scene fields + on-image text in ONE VLM pass:
    {caption, text, people, objects[], actions[], intent}."""
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path = _download(ref, tmp)
            return caption.describe(path, max_side=ref.max_side or 1024)
    except Exception as e:
        log.warning("describe failed: %s", e)
        return {"caption": "", "text": "", "people": "", "objects": [], "actions": [], "intent": ""}


class SummarizeIn(BaseModel):
    notes: str
    kind: str = "video"


@app.post("/summarize")
async def summarize_asset(body: SummarizeIn):
    """One-sentence asset summary from per-shot tags (video) or extracted text (doc)."""
    try:
        return {"summary": caption.summarize(body.notes, body.kind)}
    except Exception as e:
        log.warning("summarize failed: %s", e)
        return {"summary": ""}


class LLMFilterIn(BaseModel):
    query: str
    items: list[str]


@app.post("/llm-filter")
async def llm_filter(body: LLMFilterIn):
    """LLM relevance judge over candidate texts — returns the indices that genuinely
    match the query (multilingual, by meaning). Used on the top-K of long queries."""
    try:
        return {"relevant": caption.judge_relevance(body.query, body.items)}
    except Exception as e:
        log.warning("llm-filter failed: %s", e)
        return {"relevant": list(range(len(body.items)))}
