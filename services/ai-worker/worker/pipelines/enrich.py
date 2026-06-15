"""Shared visual enrichment for a single image or video keyframe (TSA §4.3/§4.4).

Runs face detection+recognition, object detection, and optional scene/activity
captioning via the model server, then builds frame-mapped markers, face vectors
(with name-once person clustering), object labels, and a caption.
"""
from __future__ import annotations

import uuid

import numpy as np

from .. import serving_client, stores
from ..config import settings


class InJobFaces:
    """Faces created during THIS ingest job aren't in Qdrant yet (vectors are upserted at
    job end), so `face_nearest` can't see them — which made the SAME person across a video's
    keyframes spawn dozens of separate clusters. This in-memory cache lets enrich also match a
    new face against persons already created earlier in the same job (cosine)."""

    def __init__(self) -> None:
        self._ids: list[str] = []
        self._vecs: np.ndarray | None = None

    def nearest(self, emb) -> tuple[str | None, float]:
        if not self._ids:
            return None, 0.0
        q = np.asarray(emb, dtype=float)
        q = q / (np.linalg.norm(q) + 1e-9)
        sims = self._vecs @ q
        i = int(sims.argmax())
        return self._ids[i], float(sims[i])

    def add(self, pid: str, emb) -> None:
        v = np.asarray(emb, dtype=float)
        v = v / (np.linalg.norm(v) + 1e-9)
        self._vecs = v[None, :] if self._vecs is None else np.vstack([self._vecs, v])
        self._ids.append(pid)


async def enrich(asset_id: str, storage_uri: str, filename: str, *,
                 stream_id: str | None = None, frame_index: int | None = None,
                 end_frame: int | None = None, smpte: str | None = None,
                 fps_num: int | None = None, fps_den: int | None = None,
                 do_caption: bool = True, face_cache: "InJobFaces | None" = None,
                 vlm_max_side: int | None = None) -> dict:
    """Return {markers, face_points, labels, caption}."""
    markers: list[dict] = []
    face_points: list[dict] = []

    # Faces -> name-once person clustering
    for f in serving_client.detect_faces(storage_uri, filename):
        pid, score = stores.face_nearest(f["embedding"])
        if pid is None or score < settings.face_match_threshold:
            # Not in Qdrant — try persons created earlier in THIS SAME job (their vectors
            # aren't upserted yet) so a recurring face doesn't fragment into many clusters.
            jpid, jscore = face_cache.nearest(f["embedding"]) if face_cache is not None else (None, 0.0)
            if jpid is not None and jscore >= settings.face_match_threshold:
                pid = jpid
            else:
                pid = await stores.create_person()
                if face_cache is not None:
                    face_cache.add(pid, f["embedding"])
                # Optionally crop this new person's avatar now (config: gen_face_thumbs),
                # so the People naming UI shows faces instantly instead of lazily.
                if settings.gen_face_thumbs and f.get("bbox"):
                    serving_client.face_crop(storage_uri, filename, f["bbox"], f"face-thumbs/{pid}.jpg")
        mid = str(uuid.uuid4())
        markers.append({
            "id": mid, "asset_id": asset_id, "stream_id": stream_id, "kind": "face",
            "frame_index": frame_index, "end_frame": end_frame, "smpte": smpte,
            "fps_num": fps_num, "fps_den": fps_den, "person_id": pid,
            "confidence": f["det_score"], "payload": {"bbox": f["bbox"]},
        })
        face_points.append({
            "id": mid, "vector": f["embedding"],
            "payload": {"asset_id": asset_id, "person_id": pid, "marker_id": mid,
                        "frame_index": frame_index, "smpte": smpte},
        })

    # Objects (closed-set YOLO)
    objs = serving_client.detect_objects(storage_uri, filename)
    for o in objs:
        if o["confidence"] < settings.object_min_conf:
            continue
        markers.append({
            "id": str(uuid.uuid4()), "asset_id": asset_id, "stream_id": stream_id, "kind": "object",
            "frame_index": frame_index, "end_frame": end_frame, "smpte": smpte,
            "fps_num": fps_num, "fps_den": fps_den, "label": o["label"],
            "confidence": o["confidence"], "payload": {"bbox": o["bbox"]},
        })
    labels = sorted({o["label"] for o in objs if o["confidence"] >= settings.object_min_conf})

    # Caption + on-image text in ONE VLM pass (the VLM dominates ingest time, so a
    # single combined call ~halves per-image cost vs separate caption()+ocr()).
    desc = (serving_client.describe(storage_uri, filename, max_side=vlm_max_side) if do_caption
            else {"caption": "", "text": "", "objects": [], "actions": [], "intent": ""})
    caption = desc.get("caption", "")
    ocr_text = desc.get("text", "")
    vobjects = desc.get("objects", []) or []
    vactions = desc.get("actions", []) or []
    vintent = desc.get("intent", "") or ""
    # VLM-named objects (drum, lamp…) and actions (dancing…) join the searchable labels — this
    # is how things YOLO's 80 classes can't see become findable. Deduped against YOLO labels.
    labels = sorted(set(labels) | set(vobjects) | set(vactions))
    if caption:
        markers.append({
            "id": str(uuid.uuid4()), "asset_id": asset_id, "stream_id": stream_id, "kind": "scene",
            "frame_index": frame_index, "end_frame": end_frame, "smpte": smpte,
            "fps_num": fps_num, "fps_den": fps_den, "label": caption[:200],
            # structured per-shot tags (frame-mapped) — drive object/action facets + the shot record
            "confidence": None, "payload": {"objects": vobjects, "actions": vactions, "intent": vintent},
        })

    # Dedicated OCR marker (verbatim on-image text — book pages, signboards, subtitles)
    # for display; the text is also folded into the indexable caption below.
    if ocr_text:
        markers.append({
            "id": str(uuid.uuid4()), "asset_id": asset_id, "stream_id": stream_id, "kind": "ocr",
            "frame_index": frame_index, "end_frame": end_frame, "smpte": smpte,
            "fps_num": fps_num, "fps_den": fps_den, "label": ocr_text[:200],
            "confidence": None, "payload": {"text": ocr_text[:4000]},
        })

    # Fold OCR into the indexable text so on-image text becomes searchable through the
    # existing caption-indexing path (semantic dam_text + BM25 body) — pipelines unchanged.
    indexable = caption
    if ocr_text:
        indexable = (f"{caption}\nOn-image text: {ocr_text}".strip()
                     if caption else f"On-image text: {ocr_text}")

    return {"markers": markers, "face_points": face_points, "labels": labels,
            "caption": indexable, "ocr": ocr_text,
            "objects": vobjects, "actions": vactions, "intent": vintent, "people": desc.get("people", "")}
