"""Per-asset RECALL audit for photo assets.

For every image asset, derive the searches a user would plausibly type to find IT (from its
caption: colour+garment, distinctive objects, people, on-image text, scene), run each through
the real /api/search, and check the asset is returned. Emit a per-asset PASS/MISS table and an
ERROR LIST of (asset, query, why-missed) — the recall failures.
"""
import io
import re
import sys

import httpx

sys.path.insert(0, r"E:\dam-platform\apps\api")
sys.stdout.reconfigure(encoding="utf-8")

import asyncio  # noqa: E402
from sqlalchemy import select  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Asset, Marker  # noqa: E402

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=60)
tok = s.post(f"{API}/api/auth/login",
             data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

GARMENTS = r"(?:shirt|t-?shirt|top|skirt|dupatta|saree|sari|kurta|dress|polo|jacket|blouse|" \
           r"uniform|suit|gown|coat|sweater|scarf)"
COLORS = r"(?:red|green|blue|yellow|white|black|brown|grey|gray|orange|purple|pink|maroon|" \
         r"beige|navy|teal|gold|silver)"
SCENE_NOUNS = ["building", "logo", "workflow", "sunset", "curtain", "skirt", "dupatta",
               "saree", "polo shirt", "two-story building", "data processing workflow",
               "sqlite logo", "elderly couple", "promotional event", "urban setting"]


def derive(filename: str, caption: str, labels: list, on_text: str) -> list[tuple[str, str]]:
    """Return [(query, kind)] — searches that SHOULD return this asset."""
    cap = (caption or "").lower()
    qs: list[tuple[str, str]] = []

    # 1) colour+garment phrases ("dark blue shirt"->"blue shirt", "maroon polo shirt")
    for m in re.finditer(rf"({COLORS})[\w\s-]{{0,12}}?({GARMENTS})", cap):
        qs.append((f"{m.group(1)} {m.group(2)}".replace("-", ""), "garment"))
    # bare garment if no colour caught
    for g in re.findall(GARMENTS, cap):
        qs.append((g, "garment"))

    # 2) distinctive objects from labels (skip the ubiquitous 'person')
    for lbl in labels:
        if lbl not in ("person",):
            qs.append((lbl, "object"))

    # 3) people descriptor + setting
    if "woman" in cap:
        qs.append(("a woman", "people"))
    if re.search(r"\bman\b", cap):
        qs.append(("a man", "people"))
    if "elderly" in cap and "sunset" in cap:
        qs.append(("elderly couple at sunset", "scene"))

    # 4) scene / on-image-text nouns actually present in the caption
    for noun in SCENE_NOUNS:
        if noun in cap:
            qs.append((noun, "scene"))
    # logos / named things
    if "sqlite" in cap:
        qs.append(("sqlite logo", "ocr"))
    if "two - story" in cap or "two-story" in cap:
        qs.append(("two story building", "scene"))

    # 5) identity — the person name in the filename (faces should link it)
    name = re.match(r"([A-Za-z]+)", filename)
    if name and name.group(1) not in ("Book", "ChatGPT", "Frontal", "Render", "WhatsApp",
                                      "sqlite", "Image"):
        qs.append((name.group(1), "person-name"))

    # dedup, keep order
    seen, out = set(), []
    for q, k in qs:
        key = q.lower().strip()
        if key and key not in seen:
            seen.add(key)
            out.append((q.strip(), k))
    return out


def returns(q: str, aid: str) -> tuple[bool, int, int]:
    r = s.post(f"{API}/api/search", headers=H, json={"q": q, "limit": 20}).json()
    hits = r.get("hits", [])
    for i, h in enumerate(hits):
        if h["asset_id"] == aid:
            return True, i, r.get("total", 0)
    return False, -1, r.get("total", 0)


async def main():
    async with SessionLocal() as db:
        imgs = (await db.execute(select(Asset).where(
            Asset.type == "image", Asset.deleted_at.is_(None)).order_by(Asset.filename))).scalars().all()
        from app.search import opensearch_store, constants as C
        cl = opensearch_store.client()
        rep = io.StringIO()
        errors = []
        total_q = total_miss = 0
        for a in imgs:
            aid = str(a.id)
            cap = (await db.execute(select(Marker.label).where(
                Marker.asset_id == a.id, Marker.kind == "scene").limit(1))).scalars().first()
            try:
                doc = cl.get(index=C.OS_ASSETS, id=aid)["_source"]
            except Exception:
                doc = {}
            queries = derive(a.filename, cap or "", doc.get("labels") or [], doc.get("visual_text") or "")
            if not queries:
                rep.write(f"\n■ {a.filename}  (no derivable queries — caption empty)\n")
                continue
            rep.write(f"\n■ {a.filename}\n")
            for q, kind in queries:
                total_q += 1
                ok, rank, n = returns(q, aid)
                mark = f"rank {rank+1}/{n}" if ok else "** MISS **"
                rep.write(f"    [{ 'ok ' if ok else 'MISS'}] {kind:11} {q!r:28} -> {mark}\n")
                if not ok:
                    total_miss += 1
                    errors.append((a.filename, kind, q, n))
        rep.write(f"\n{'='*70}\nQUERIES: {total_q}  MISSES: {total_miss}\n\nERROR LIST (recall failures):\n")
        for fn, kind, q, n in errors:
            rep.write(f"  ✗ {fn[:30]:30} | {kind:11} | query={q!r:28} | search returned {n} other(s)\n")
        out = rep.getvalue()
        print(out)
        with open(r"E:\dam-platform\.data\audit_photo_recall.txt", "w", encoding="utf-8") as fh:
            fh.write(out)

asyncio.run(main())
