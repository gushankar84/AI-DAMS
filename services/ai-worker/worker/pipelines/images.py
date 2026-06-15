"""Image pipeline (TSA §4.3).

P1 (now):  semantic image embedding (CLIP, server-side) + deterministic thumbnail + BM25 doc.
P3 (next): InsightFace faces -> person clustering, YOLO/open-vocab objects,
           Qwen3-VL scene/activity captioning, embedded-text OCR (all server-side).

Identity-fidelity (§7): thumbnails use classical Lanczos only — no generative model.
"""
from __future__ import annotations

import logging
import os
import tempfile
import uuid

import uuid as _uuid

from .. import serving_client, stores
from ..config import QDRANT_FACE, QDRANT_IMAGE, QDRANT_TEXT
from .enrich import InJobFaces, enrich

log = logging.getLogger("dam.pipeline.images")

THUMB_MAX = 512


def _make_thumbnail(src_path: str, dst_path: str) -> None:
    from PIL import Image
    with Image.open(src_path) as im:
        im = im.convert("RGB")
        im.thumbnail((THUMB_MAX, THUMB_MAX), Image.LANCZOS)  # deterministic, non-generative
        im.save(dst_path, "JPEG", quality=85)


MAX_REGIONS = 6


def _region_points(local_path: str, asset_id: str, boxes: list[list[float]]) -> list[dict]:
    """Crop each person box (padded), embed it, and return dam_image points tagged
    region='person'. Tiny boxes are skipped. Failures are swallowed per-region."""
    import base64
    import io

    from PIL import Image

    points: list[dict] = []
    try:
        with Image.open(local_path) as im0:
            im0 = im0.convert("RGB")
            w, h = im0.size
            # largest boxes first (main subjects)
            boxes = sorted(boxes, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]), reverse=True)[:MAX_REGIONS]
            for b in boxes:
                x1, y1, x2, y2 = b
                bw, bh = x2 - x1, y2 - y1
                if bw < 48 or bh < 48 or (bw * bh) < 0.02 * w * h:
                    continue  # too small to be useful
                px, py = bw * 0.08, bh * 0.08  # slight padding for context
                crop = im0.crop((max(0, int(x1 - px)), max(0, int(y1 - py)),
                                 min(w, int(x2 + px)), min(h, int(y2 + py))))
                buf = io.BytesIO()
                crop.save(buf, "JPEG", quality=88)
                b64 = base64.b64encode(buf.getvalue()).decode()
                vec = serving_client.embed_image_b64(b64)
                points.append({
                    "id": str(uuid.uuid4()), "vector": vec,
                    "payload": {"asset_id": asset_id, "asset_type": "image",
                                "region": "person", "bbox": [float(x) for x in b]},
                })
    except Exception as e:
        log.warning("region embedding failed for %s: %s", asset_id, e)
    return points


async def process(asset: dict) -> None:
    asset_id = asset["id"]
    await stores.set_status(asset_id, "processing")
    await stores.set_status(asset_id, "extracting")

    thumb_uri = None
    img_points: list[dict] = []
    labels: list[str] = []
    caption = ""
    marker_count = 0

    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, asset["filename"])
        try:
            stores.download_to(asset["storage_uri"], local)
        except Exception as e:
            log.warning("download failed for %s: %s", asset_id, e)
            local = None

        # Deterministic thumbnail (CPU/PIL — Lanczos only, non-generative).
        if local:
            try:
                thumb_local = os.path.join(tmp, "thumb.jpg")
                _make_thumbnail(local, thumb_local)
                thumb_uri = stores.upload_file(thumb_local, f"image/{asset_id}/thumb.jpg", "image/jpeg")
            except Exception as e:
                log.warning("thumbnail failed for %s: %s", asset_id, e)

        # Whole-image semantic embedding (SigLIP) via the model server.
        try:
            vec = serving_client.embed_image(asset["storage_uri"], asset["filename"])
            img_points.append({
                "id": str(uuid.uuid4()), "vector": vec,
                "payload": {"asset_id": asset_id, "asset_type": "image", "region": "full",
                            "department": asset.get("department"), "project": asset.get("project")},
            })
        except Exception as e:
            log.warning("image embed failed for %s: %s", asset_id, e)

        # Faces + objects + scene caption (markers, face vectors, labels).
        try:
            e = await enrich(asset_id, asset["storage_uri"], asset["filename"], face_cache=InJobFaces())
            marker_count = len(e["markers"])
            if e["markers"]:
                await stores.insert_markers(e["markers"])
            if e["face_points"]:
                stores.upsert_vectors(QDRANT_FACE, e["face_points"])
            labels = e["labels"]
            caption = e["caption"]

            # Fine image detail (attributes, scene, relationships, on-image text) is
            # made searchable by the VLM caption below — embedded as text — rather than
            # by per-region crops (which bloat the index and don't bind attributes).
            # caption -> semantic (dam_text) so natural-language scene queries match
            if caption:
                cvec = serving_client.embed_texts([caption])[0]
                stores.upsert_vectors(QDRANT_TEXT, [{
                    "id": str(_uuid.uuid4()), "vector": cvec,
                    "payload": {"asset_id": asset_id, "asset_type": "image",
                                "snippet": caption[:300]},
                }])
        except Exception as ex:
            log.warning("image enrichment skipped for %s: %s", asset_id, ex)

    if img_points:
        stores.upsert_vectors(QDRANT_IMAGE, img_points)
    if thumb_uri:
        pool = await stores.pg()
        await pool.execute("UPDATE asset SET thumbnail_uri=$2 WHERE id=$1", asset_id, thumb_uri)

    # Body = AI-derived text only (caption + object labels). The filename is NOT
    # indexed as body — dates/app names ("WhatsApp Image", "Jan") cause spurious
    # fuzzy keyword matches. Filename stays in `title` for display.
    body_parts = [caption, " ".join(labels)]
    stores.index_asset_doc({
        "asset_id": asset_id, "asset_type": "image",
        "title": asset.get("title") or asset["filename"],
        "description": caption or asset.get("description"),
        "body": " ".join(p for p in body_parts if p), "summary": caption,
        "visual_text": " ".join(p for p in body_parts if p)[:200_000],   # images: everything is SEEN
        "tags": asset.get("tags") or [], "labels": labels,
        "department": asset.get("department"), "project": asset.get("project"),
        "language": asset.get("language"),
        "created_at": asset["created_at"].isoformat() if asset.get("created_at") else None,
    })

    if caption:
        await stores.set_description(asset_id, caption)   # the structured caption doubles as the image's summary
    await stores.set_status(asset_id, "searchable")
    log.info("image indexed: %s (%d markers, %d labels)", asset_id, marker_count, len(labels))
