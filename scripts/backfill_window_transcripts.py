"""Backfill: re-group per-segment transcript dense vectors in dam_text into specific units.

WHY: audio/video used to embed ONE dense vector per ASR segment. Short fillers ("sorry sir",
"मैं वो", "🎵") became standalone near-centroid vectors that clear the relevance floor for
almost any query — a search "black hole" (rashmika_hindi_audio surfaced for airplane/pizza/
spaceship/horse). The pipelines now group segments (window_segments: substantial segments
stand alone, short runs merge); this backfill applies the same fix to already-indexed assets
WITHOUT re-running ASR.

SOURCE OF TRUTH: the Postgres `transcript` table (all per-segment rows survive). For each
audio/video asset we delete its existing transcript dense points (kind=='transcript', or a
legacy per-segment point whose snippet equals a transcript row) and re-create grouped units.
Scene-caption points are left untouched. Re-runnable / idempotent.

Run:  apps/api/.venv/Scripts/python.exe scripts/backfill_window_transcripts.py [--apply]
Default is DRY-RUN (prints the plan, mutates nothing).
"""
import sys
import uuid

sys.path.insert(0, r"E:\dam-platform\apps\api")
sys.stdout.reconfigure(encoding="utf-8")

import asyncio  # noqa: E402
import re  # noqa: E402

from qdrant_client.http import models as qm  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.models import Asset, Transcript  # noqa: E402
from app.search import constants as C, embed_client, qdrant_store  # noqa: E402

APPLY = "--apply" in sys.argv


def is_informative(text: str) -> bool:
    return bool(re.search(r"[^\W\d_]{2,}", text or ""))


def window_segments(seg_texts, anchors, min_words=6):
    """Mirror of worker/pipelines/common.window_segments: a substantial segment (>= min_words)
    stands alone; runs of short segments merge until they reach min_words. Non-informative
    units dropped. (Copied to avoid a cross-venv import.)"""
    windows, buf, anchor, wc = [], [], None, 0

    def flush():
        nonlocal buf, anchor, wc
        if buf:
            windows.append((" ".join(buf), anchor))
            buf, anchor, wc = [], None, 0

    for text, anc in zip(seg_texts, anchors):
        if len(text.split()) >= min_words:
            flush()
            windows.append((text, anc))
        else:
            if not buf:
                anchor = anc
            buf.append(text)
            wc += len(text.split())
            if wc >= min_words:
                flush()
    flush()
    return [(t, a) for (t, a) in windows if is_informative(t)]


def scroll_points(asset_id: str):
    qc = qdrant_store.client()
    out, offset = [], None
    while True:
        pts, offset = qc.scroll(
            collection_name=C.QDRANT_TEXT,
            scroll_filter=qm.Filter(must=[qm.FieldCondition(
                key="asset_id", match=qm.MatchValue(value=asset_id))]),
            limit=256, offset=offset, with_payload=True, with_vectors=False)
        out += [(p.id, p.payload or {}) for p in pts]
        if offset is None:
            break
    return out


async def main():
    qc = qdrant_store.client()
    async with SessionLocal() as db:
        assets = (await db.execute(select(Asset).where(
            Asset.type.in_(["audio", "video"]), Asset.deleted_at.is_(None)))).scalars().all()
        print(f"{'APPLY' if APPLY else 'DRY-RUN'} — {len(assets)} audio/video assets\n")
        tot_del = tot_new = touched = 0
        for a in assets:
            aid = str(a.id)
            trows = (await db.execute(select(Transcript.text, Transcript.start_frame)
                                      .where(Transcript.asset_id == a.id)
                                      .order_by(Transcript.start_frame))).all()
            if not trows:
                continue
            seg_texts = [(t or "").strip() for t, _ in trows if (t or "").strip()]
            anchors = [sf for t, sf in trows if (t or "").strip()]
            if not seg_texts:
                continue
            trans_set = {t[:300] for t in seg_texts}
            points = scroll_points(aid)
            # OLD transcript dense points to remove: prior windows (kind=='transcript') OR legacy
            # per-segment points (snippet is a transcript row). Scene captions are kept.
            old_ids = [pid for pid, pl in points
                       if pl.get("kind") == "transcript"
                       or ((pl.get("snippet") or "") in trans_set and pl.get("start_frame") is not None)]
            units = window_segments(seg_texts, anchors)
            if not old_ids and not units:
                continue
            touched += 1
            tot_del += len(old_ids)
            tot_new += len(units)
            print(f"  {a.type:5} {a.filename[:32]:32} {len(seg_texts):>3} segs -> {len(units):>2} units "
                  f"(drop {len(old_ids)} old)")
            if not APPLY:
                continue
            new_points = []
            for text, sf in units:
                vec = embed_client.embed_text(text)
                if not vec:
                    print(f"      ! embed failed for {aid[:8]}")
                    continue
                new_points.append(qm.PointStruct(
                    id=str(uuid.uuid4()), vector=vec,
                    payload={"asset_id": aid, "asset_type": a.type, "kind": "transcript",
                             "department": a.department, "project": a.project,
                             "snippet": text[:300], "start_frame": sf, "smpte": None}))
            if new_points:
                qc.upsert(collection_name=C.QDRANT_TEXT, points=new_points)
            if old_ids:
                qc.delete(collection_name=C.QDRANT_TEXT,
                          points_selector=qm.PointIdsList(points=old_ids))
        print(f"\n{'APPLIED' if APPLY else 'WOULD CHANGE'}: {touched} assets, "
              f"-{tot_del} old transcript vectors, +{tot_new} grouped units")
        if not APPLY:
            print("Re-run with --apply to write.")


asyncio.run(main())
