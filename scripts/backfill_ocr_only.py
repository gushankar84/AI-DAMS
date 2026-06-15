r"""#3 — OCR-only incremental backfill.

Adds on-image text to assets that were enriched BEFORE OCR existed, WITHOUT a full
reprocess (skips faces/objects/caption that already exist). One VLM call per image;
indexes the text as a semantic point (+ display marker + best-effort BM25 append).
Idempotent: skips any asset that already has an 'ocr' marker.

Run in the ai-worker venv:  services/ai-worker/.venv/Scripts/python.exe scripts/backfill_ocr_only.py
"""
import asyncio
import time
import uuid

from worker import serving_client, stores
from worker.config import QDRANT_TEXT

NS = uuid.UUID("a1b2c3d4-0000-4000-8000-000000000002")


async def main():
    pool = await stores.pg()
    rows = await pool.fetch(
        "SELECT id, filename, storage_uri, type FROM asset "
        "WHERE type='image' AND deleted_at IS NULL ORDER BY filename")
    added = 0
    t0 = time.perf_counter()
    for r in rows:
        aid = r["id"]
        if await pool.fetchval("SELECT 1 FROM marker WHERE asset_id=$1 AND kind='ocr' LIMIT 1", aid):
            continue  # already has OCR — idempotent
        text = serving_client.ocr(r["storage_uri"], r["filename"])
        if not text:
            continue
        # 1) display marker
        await stores.insert_markers([{
            "id": str(uuid.uuid4()), "asset_id": aid, "kind": "ocr",
            "label": text[:200], "payload": {"text": text[:4000]}}])
        # 2) semantic point — searchable without a full reprocess
        snippet = f"On-image text: {text}"
        vec = serving_client.embed_texts([snippet])[0]
        stores.upsert_vectors(QDRANT_TEXT, [{
            "id": str(uuid.uuid5(NS, f"{aid}:ocr")), "vector": vec,
            "payload": {"asset_id": aid, "asset_type": r["type"], "snippet": snippet[:300]}}])
        # 3) best-effort BM25 append (keyword search)
        try:
            os_assets = getattr(stores, "OS_ASSETS", "dam_assets")
            stores.opensearch().update(index=os_assets, id=aid, body={"script": {
                "source": "ctx._source.body = (ctx._source.containsKey('body') && "
                          "ctx._source.body != null ? ctx._source.body : '') + params.t",
                "params": {"t": " " + text}}}, refresh=True)
        except Exception as e:
            print(f"  (BM25 append skipped: {e})")
        added += 1
        print(f"  OCR+index {r['filename'][:30]:30} {text[:45]!r}")
    dt = time.perf_counter() - t0
    print(f"\nOCR-only backfill: added text to {added} image(s) in {dt:.1f}s (no full reprocess)")


asyncio.run(main())
