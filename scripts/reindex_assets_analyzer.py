"""Recreate the dam-assets index with the new analyzer (word_delimiter + kstem) and reload it.

The document _source is unchanged — only the index-time tokenization changes — so we scroll the
existing docs, drop+recreate the index from ASSET_MAPPING, and bulk re-insert. No PG/model work.
Idempotent. Run: apps/api/.venv/Scripts/python.exe scripts/reindex_assets_analyzer.py
"""
import sys
sys.path.insert(0, r"E:\dam-platform\apps\api")
sys.stdout.reconfigure(encoding="utf-8")

from app.search import opensearch_store as oss, constants as C  # noqa: E402

cl = oss.client()
idx = C.OS_ASSETS

# 1) scroll every doc (id + source)
docs = []
res = cl.search(index=idx, body={"query": {"match_all": {}}}, scroll="2m", size=500)
sid = res.get("_scroll_id")
hits = res["hits"]["hits"]
while hits:
    docs += [(h["_id"], h["_source"]) for h in hits]
    res = cl.scroll(scroll_id=sid, scroll="2m")
    sid = res.get("_scroll_id")
    hits = res["hits"]["hits"]
print(f"scrolled {len(docs)} docs from {idx}")

# 2) drop + recreate with the new mapping/analyzer
if cl.indices.exists(index=idx):
    cl.indices.delete(index=idx)
cl.indices.create(index=idx, body=oss.ASSET_MAPPING)
print(f"recreated {idx} with analyzer dam_text")

# 3) re-insert (same _source, re-tokenized by the new analyzer)
for did, src in docs:
    cl.index(index=idx, id=did, body=src)
cl.indices.refresh(index=idx)
print(f"re-inserted {len(docs)} docs; index count = {cl.count(index=idx)['count']}")

# 4) prove the analyzer does what we want
for term, text in [("stella", "Stella1.jpg"), ("curtain", "beige curtains"), ("police", "pole")]:
    toks = [t["token"] for t in cl.indices.analyze(
        index=idx, body={"analyzer": "dam_text", "text": text})["tokens"]]
    print(f"   analyze({text!r}) -> {toks}   (query {term!r} {'MATCHES' if term in toks else 'NO MATCH'})")
