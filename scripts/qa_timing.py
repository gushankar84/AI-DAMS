r"""Timing report: indexing (per asset type, end-to-end + stage breakdown) and search.
Run from the ai-worker venv (needs worker.config + asyncpg)."""
import asyncio
import io
import statistics
import time

import asyncpg
import httpx

from worker.config import settings

SRV = "http://127.0.0.1:8100"
API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=600)
rep = io.StringIO()


def med(fn, n=3):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter(); fn(); xs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(xs)


def once(fn):
    t0 = time.perf_counter(); fn(); return (time.perf_counter() - t0) * 1000


async def main():
    dsn = settings.database_url.replace("+asyncpg", "")
    conn = await asyncpg.connect(dsn)
    a = {}
    for t in ("document", "image", "audio", "video"):
        r = await conn.fetchrow(
            "SELECT id::text, filename, storage_uri FROM asset "
            "WHERE type=$1 AND status='searchable' AND deleted_at IS NULL LIMIT 1", t)
        if r:
            a[t] = dict(r)
    await conn.close()

    tok = s.post(f"{API}/api/auth/login",
                 data={"username": "admin@dam.local", "password": "admin12345"},
                 headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    H = {"Authorization": f"Bearer {tok}"}

    def ref(t):
        return {"storage_uri": a[t]["storage_uri"], "filename": a[t]["filename"]}

    # ── per-stage model-server timings ──
    rep.write("=== INDEXING — per-stage (model server) ===\n")
    if "document" in a:
        rep.write(f"  doc  parse (Docling)     : {once(lambda: s.post(f'{SRV}/parse/document', json=ref('document'))):8.0f} ms\n")
    if "image" in a:
        rep.write(f"  img  SigLIP embed        : {med(lambda: s.post(f'{SRV}/embed/image', json=ref('image'))):8.0f} ms\n")
        rep.write(f"  img  faces (InsightFace) : {med(lambda: s.post(f'{SRV}/faces', json=ref('image')), 2):8.0f} ms\n")
        rep.write(f"  img  objects (YOLOv11)   : {med(lambda: s.post(f'{SRV}/objects', json=ref('image')), 2):8.0f} ms\n")
        rep.write(f"  img  caption (Qwen3-VL)  : {once(lambda: s.post(f'{SRV}/caption', json=ref('image'))):8.0f} ms\n")
    if "audio" in a:
        rep.write(f"  aud  ASR (Whisper lv3)   : {once(lambda: s.post(f'{SRV}/asr', json=ref('audio'))):8.0f} ms\n")
    rep.write(f"  any  BGE-M3 text embed    : {med(lambda: s.post(f'{SRV}/embed/texts', json={'texts': ['a man in a blue shirt']})):8.0f} ms\n")

    # ── end-to-end reprocess per type ──
    rep.write("\n=== INDEXING — end-to-end (trigger -> searchable) ===\n")
    for t in ("document", "image", "audio", "video"):
        if t not in a:
            continue
        t0 = time.perf_counter()
        s.post(f"{API}/api/assets/{a[t]['id']}/reprocess", headers=H)
        st = "?"
        for _ in range(120):
            st = s.get(f"{API}/api/assets/{a[t]['id']}", headers=H).json()["status"]
            if st in ("searchable", "failed"):
                break
            time.sleep(2)
        dt = (time.perf_counter() - t0) * 1000
        rep.write(f"  {t:9s} {a[t]['filename'][:32]:32s} : {dt:8.0f} ms  ({st})\n")

    # ── search latency ──
    rep.write("\n=== SEARCH — query latency ===\n")
    def search(q, types=None):
        b = {"q": q, "limit": 5, "rerank": True}
        if types:
            b["types"] = types
        s.post(f"{API}/api/search", headers=H, json=b)
    for label, q, ty in [("image (all signals)", "a man in a blue shirt", None),
                         ("document (semantic)", "media asset management", ["document"]),
                         ("audio (transcript)", "an orphan in a temple", ["audio"]),
                         ("image-only filter", "green saree", ["image"])]:
        rep.write(f"  {label:22s} '{q[:24]:24s}': {med(lambda: search(q, ty), 5):6.0f} ms\n")

    with open(r"E:\dam-platform\.data\qa_timing_report.txt", "w", encoding="utf-8") as f:
        f.write(rep.getvalue())
    print(rep.getvalue())


asyncio.run(main())
