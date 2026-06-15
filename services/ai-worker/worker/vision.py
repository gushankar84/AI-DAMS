"""Vision models (TSA §4.3/§4.4) — faces (InsightFace) and objects (YOLO).

Lives in the serving process. InsightFace runs on onnxruntime (CPU by default —
fine for images; configurable to GPU later); YOLO runs on torch/CUDA.
"""
from __future__ import annotations

import logging
import threading

from .config import settings

log = logging.getLogger("dam.vision")
_lock = threading.Lock()
_face_app = None
_yolo = None


# ─── Faces: RetinaFace detection + ArcFace embedding ───────────────────────
def _faces_model():
    global _face_app
    if _face_app is None:
        with _lock:
            if _face_app is None:
                from insightface.app import FaceAnalysis
                providers = ["CPUExecutionProvider"]
                app = FaceAnalysis(name="buffalo_l", providers=providers,
                                   allowed_modules=["detection", "recognition"])
                app.prepare(ctx_id=-1, det_size=(640, 640))
                _face_app = app
                log.info("InsightFace buffalo_l ready (%s)", providers)
    return _face_app


def detect_faces(image_path: str) -> list[dict]:
    """Return [{bbox:[x1,y1,x2,y2], det_score, embedding:[512]}] for each face."""
    import cv2
    img = cv2.imread(image_path)
    if img is None:
        return []
    out = []
    for f in _faces_model().get(img):
        emb = f.normed_embedding
        out.append({
            "bbox": [float(x) for x in f.bbox.tolist()],
            "det_score": float(f.det_score),
            "embedding": [float(x) for x in emb.tolist()],
        })
    return out


# ─── Objects: YOLO (closed-set) ────────────────────────────────────────────
def _yolo_model():
    global _yolo
    if _yolo is None:
        with _lock:
            if _yolo is None:
                from ultralytics import YOLO
                _yolo = YOLO(settings.yolo_model)
                log.info("YOLO %s ready", settings.yolo_model)
    return _yolo


def detect_objects(image_path: str, conf: float = 0.35) -> list[dict]:
    """Return [{label, confidence, bbox:[x1,y1,x2,y2]}] for detected objects."""
    model = _yolo_model()
    dev = settings.yolo_device if (settings.yolo_device != "cuda" or _cuda()) else "cpu"
    results = model.predict(image_path, conf=conf, device=dev, verbose=False)
    out = []
    for r in results:
        names = r.names
        for b in r.boxes:
            cls = int(b.cls[0])
            out.append({
                "label": names.get(cls, str(cls)),
                "confidence": float(b.conf[0]),
                "bbox": [float(x) for x in b.xyxy[0].tolist()],
            })
    return out


def _cuda() -> bool:
    try:
        import torch
        return torch.cuda.is_available()
    except Exception:
        return False


def release() -> None:
    """Free YOLO GPU memory (InsightFace is CPU)."""
    global _yolo
    with _lock:
        _yolo = None
    import gc
    gc.collect()
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
