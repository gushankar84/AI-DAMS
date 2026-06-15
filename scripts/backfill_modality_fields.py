"""Backfill source-separated modality fields (visual_text / spoken_text) into the
OpenSearch asset docs — rebuilt from Postgres (scene/OCR markers + transcripts), so NO
model re-runs are needed. Adds the new mapping fields, then partial-updates every doc.

After this, a keyword hit can be attributed: 'police' SEEN in a frame vs SAID in speech.
"""
import asyncio

import asyncpg
import httpx
from opensearchpy import OpenSearch

OS_URL = "http://localhost:9200"
INDEX = "dam-assets"
PG = "postgresql://dam:dam_dev_pw@localhost:5432/dam"


async def main():
    os_ = OpenSearch(hosts=[OS_URL], timeout=30)
    # 1) extend the live index mapping (additive — existing fields untouched)
    os_.indices.put_mapping(index=INDEX, body={"properties": {
        "visual_text": {"type": "text"},
        "spoken_text": {"type": "text"},
    }})
    print("mapping extended")

    pool = await asyncpg.create_pool(PG, min_size=1, max_size=3)
    assets = await pool.fetch("SELECT id, type FROM asset WHERE deleted_at IS NULL")
    done = skipped = 0
    for a in assets:
        aid = str(a["id"])
        # what is SEEN: scene captions + OCR text + object labels (incl. VLM payload tags)
        scenes = await pool.fetch(
            "SELECT label, payload FROM marker WHERE asset_id=$1 AND kind IN ('scene','ocr','object')", aid)
        vparts: list[str] = []
        for m in scenes:
            if m["label"]:
                vparts.append(m["label"])
            p = m["payload"]
            if p:
                import json
                try:
                    pj = json.loads(p) if isinstance(p, str) else p
                    vparts += pj.get("objects", []) or []
                    vparts += pj.get("actions", []) or []
                    if pj.get("intent"):
                        vparts.append(pj["intent"])
                    if pj.get("text"):
                        vparts.append(pj["text"])
                except Exception:
                    pass
        # what is SAID: the transcript
        segs = await pool.fetch("SELECT text FROM transcript WHERE asset_id=$1", aid)
        sparts = [s["text"] for s in segs if s["text"]]
        if not vparts and not sparts:
            skipped += 1
            continue
        try:
            os_.update(index=INDEX, id=aid, body={"doc": {
                "visual_text": " ".join(dict.fromkeys(vparts))[:200_000],
                "spoken_text": " ".join(sparts)[:200_000],
            }})
            done += 1
        except Exception as e:
            print(f"  skip {aid}: {e}")
    os_.indices.refresh(index=INDEX)
    print(f"backfilled {done} assets ({skipped} had no derived text)")
    await pool.close()

asyncio.run(main())
