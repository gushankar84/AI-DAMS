"""Lazy-loaded model singletons (the model-serving plane for the MVP).

Models are loaded on first use so the worker process can start — and report
capabilities — before multi-GB weights finish downloading. In P6 these move
behind vLLM + Text-Embeddings-Inference; callers here stay unchanged.
"""
from __future__ import annotations

import logging
import threading

from .config import settings

log = logging.getLogger("dam.models")
_lock = threading.Lock()
_cache: dict[str, object] = {}


def _device() -> str:
    if settings.ai_device == "cuda":
        try:
            import torch
            if torch.cuda.is_available():
                return "cuda"
            log.warning("CUDA requested but unavailable; falling back to CPU")
        except Exception:
            pass
    return "cpu"


def capabilities() -> dict[str, bool]:
    caps = {}
    for mod in ("torch", "sentence_transformers", "open_clip"):
        try:
            __import__(mod)
            caps[mod] = True
        except Exception:
            caps[mod] = False
    return caps


# ─── Text embeddings (BGE-M3) ──────────────────────────────────────────────
def _text_model():
    if "text" not in _cache:
        with _lock:
            if "text" not in _cache:
                from sentence_transformers import SentenceTransformer
                log.info("loading text embedder %s on %s", settings.text_embed_model, _device())
                _cache["text"] = SentenceTransformer(settings.text_embed_model, device=_device())
    return _cache["text"]


def embed_text(text: str) -> list[float]:
    model = _text_model()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.tolist()


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = _text_model()
    return [v.tolist() for v in model.encode(texts, normalize_embeddings=True, batch_size=16)]


# ─── Image embedder (image + text shared space) — RESIDENT ────────────────
# Uses open_clip (proven encode_image/encode_text path). Tries SigLIP 2 first
# (multilingual, SOTA retrieval), then SigLIP v1, then a strong CLIP — whichever
# is available in the installed open_clip. All are 768-dim so the index is stable.
_IMG_CANDIDATES = [
    ("ViT-B-16-SigLIP2-512", "webli"),   # SigLIP 2 — multilingual, best
    ("ViT-B-16-SigLIP-512", "webli"),    # SigLIP v1
    ("ViT-B-16-SigLIP", "webli"),
    ("ViT-L-14", "openai"),              # strong CLIP fallback (768-d)
]


def _clip():
    if "clip" not in _cache:
        with _lock:
            if "clip" not in _cache:
                import open_clip
                dev = _device()
                last = None
                for name, pre in _IMG_CANDIDATES:
                    try:
                        model, _, preprocess = open_clip.create_model_and_transforms(
                            name, pretrained=pre, device=dev)
                        tokenizer = open_clip.get_tokenizer(name)
                        model.eval()
                        log.info("image embedder loaded: %s / %s (dim via probe)", name, pre)
                        _cache["clip"] = (model, preprocess, tokenizer, dev, name)
                        break
                    except Exception as e:
                        last = e
                        log.warning("image model %s/%s unavailable: %s", name, pre, e)
                else:
                    raise RuntimeError(f"no image embedder could load: {last}")
    return _cache["clip"]


def image_model_name() -> str:
    return _clip()[4]


def embed_clip_text(text: str) -> list[float]:
    """Text → shared image space (for text→image search)."""
    import torch
    model, _, tokenizer, dev, _name = _clip()
    with torch.no_grad():
        feats = model.encode_text(tokenizer([text]).to(dev))
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].float().cpu().tolist()


def embed_image(pil_image) -> list[float]:
    import torch
    model, preprocess, _tok, dev, _name = _clip()
    img = preprocess(pil_image.convert("RGB")).unsqueeze(0).to(dev)
    with torch.no_grad():
        feats = model.encode_image(img)
        feats = feats / feats.norm(dim=-1, keepdim=True)
    return feats[0].float().cpu().tolist()


# ─── Cross-encoder reranker (P4) ───────────────────────────────────────────
def _reranker():
    if "rerank" not in _cache:
        with _lock:
            if "rerank" not in _cache:
                from sentence_transformers import CrossEncoder
                log.info("loading reranker %s on %s", settings.rerank_model, _device())
                _cache["rerank"] = CrossEncoder(settings.rerank_model, device=_device(), max_length=512)
    return _cache["rerank"]


def rerank(query: str, passages: list[str]) -> list[float]:
    """Cross-encoder relevance scores for (query, passage) pairs."""
    if not passages:
        return []
    scores = _reranker().predict([(query, p) for p in passages])
    return [float(s) for s in scores]
