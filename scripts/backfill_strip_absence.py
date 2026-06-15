"""Backfill: remove 'absence of X' sentences from already-indexed captions so absent objects
stop being searchable (search "car" was returning a photo captioned "...no cars are visible").

Cleans in place: OS body/visual_text/description/summary, PG scene-marker labels +
asset.description, and re-embeds any dam_text dense vector whose snippet held an absence
sentence. Mirrors worker.caption.strip_absence. Idempotent. Run from the API venv:
    apps/api/.venv/Scripts/python.exe scripts/backfill_strip_absence.py [--apply]
"""
import re
import sys

sys.path.insert(0, r"E:\dam-platform\apps\api")
sys.stdout.reconfigure(encoding="utf-8")

import asyncio  # noqa: E402
from sqlalchemy import select, update  # noqa: E402
from qdrant_client.http import models as qm  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Asset, Marker  # noqa: E402
from app.search import opensearch_store, qdrant_store, embed_client, constants as C  # noqa: E402

APPLY = "--apply" in sys.argv

# Kept identical to worker/caption.py strip_absence (lead-negation + presence verb).
_LEAD_NEG = re.compile(r"""^\s*["'“”']?\s*(?:there\s+(?:are|is)\s+)?(?:no|none|nothing|without)\b""", re.I)
_PRESENCE = (r"\b(?:visible|present|presence|seen|depicted|observable|observed|detected|"
             r"discernible|displayed|shown)\b")
_SENT = re.compile(r"[^.!?]*[.!?]")


def _is_absence(s):
    return bool(_LEAD_NEG.search(s) and re.search(_PRESENCE, s, re.I))


def has_absence(t):
    if not t:
        return False
    return any(_is_absence(s) for s in _SENT.findall(t)) or _is_absence(t)


def strip_absence(text):
    if not text:
        return text
    kept = [s for s in _SENT.findall(text) if not _is_absence(s)]
    out = " ".join(s.strip() for s in kept).strip()
    tail = _SENT.sub("", text).strip()
    if tail and not _is_absence(tail):
        out = f"{out} {tail}".strip()
    return out


async def main():
    cl = opensearch_store.client()
    qc = qdrant_store.client()
    n_os = n_mk = n_vec = 0

    async with SessionLocal() as db:
        for mid, lbl in (await db.execute(select(Marker.id, Marker.label).where(
                Marker.kind == "scene", Marker.label.is_not(None)))).all():
            if has_absence(lbl):
                n_mk += 1
                if APPLY:
                    await db.execute(update(Marker).where(Marker.id == mid).values(label=strip_absence(lbl)))
        for aid, d in (await db.execute(select(Asset.id, Asset.description).where(
                Asset.description.is_not(None)))).all():
            if has_absence(d) and APPLY:
                await db.execute(update(Asset).where(Asset.id == aid).values(description=strip_absence(d)))
        if APPLY:
            await db.commit()

    # Captions live on image/video assets; a DOCUMENT body's "no X" is real content — never touch it.
    res = cl.search(index=C.OS_ASSETS, body={
        "query": {"terms": {"asset_type": ["image", "video"]}}, "size": 500})
    for h in res["hits"]["hits"]:
        src = h["_source"]
        patch = {f: strip_absence(src.get(f)) for f in ("body", "visual_text", "description", "summary")
                 if has_absence(src.get(f))}
        if patch:
            n_os += 1
            print(f"  OS  {src.get('title','?')[:30]:30} fields={list(patch)}")
            if APPLY:
                cl.update(index=C.OS_ASSETS, id=h["_id"], body={"doc": patch})

    offset, to_fix = None, []
    while True:
        pts, offset = qc.scroll(collection_name=C.QDRANT_TEXT, limit=512, offset=offset,
                                with_payload=True, with_vectors=False)
        to_fix += [p for p in pts if (p.payload or {}).get("asset_type") in ("image", "video")
                   and has_absence(p.payload.get("snippet"))]
        if offset is None:
            break
    for p in to_fix:
        clean = strip_absence(p.payload["snippet"])
        n_vec += 1
        print(f"  VEC {str(p.id)[:8]} {p.payload['snippet'][:38]!r} -> {clean[:38]!r}")
        if APPLY and clean.strip():
            vec = embed_client.embed_text(clean)
            newpl = dict(p.payload); newpl["snippet"] = clean[:300]
            qc.upsert(collection_name=C.QDRANT_TEXT,
                      points=[qm.PointStruct(id=p.id, vector=vec, payload=newpl)])
    if APPLY:
        cl.indices.refresh(index=C.OS_ASSETS)
    print(f"\n{'APPLIED' if APPLY else 'WOULD CLEAN'}: {n_mk} scene markers, {n_os} OS docs, {n_vec} dense vectors")
    if not APPLY:
        print("Re-run with --apply to write.")


asyncio.run(main())
