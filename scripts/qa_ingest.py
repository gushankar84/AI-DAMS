r"""Production QA — ingestion robustness & idempotency.
- reprocess must be idempotent (clear_derived: vector counts stay stable, no orphans)
- no-audio video must reach 'searchable' (not 'failed') via visual signals
- no asset stuck in a non-terminal status
"""
import io
import time

import httpx

API = "http://127.0.0.1:8000"
QD = "http://localhost:6333"
s = httpx.Client(timeout=120)
tok = s.post(f"{API}/api/auth/login",
             data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

rep = io.StringIO()
passes = fails = 0


def check(name, cond, detail=""):
    global passes, fails
    ok = bool(cond); passes += ok; fails += (not ok)
    rep.write(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}\n")


def vec_count(coll, asset_id):
    r = s.post(f"{QD}/collections/{coll}/points/count",
               json={"filter": {"must": [{"key": "asset_id", "match": {"value": asset_id}}]}, "exact": True})
    return r.json()["result"]["count"]


assets = s.get(f"{API}/api/assets?limit=200", headers=H).json()
rep.write(f"total assets: {len(assets)}\n")

# 1) terminal status — nothing stuck
non_terminal = [a for a in assets if a["status"] not in ("searchable", "failed")]
check("no asset stuck in non-terminal status", len(non_terminal) == 0,
      f"stuck={[a['filename'] for a in non_terminal][:5]}")

# 2) failures
failed = [a for a in assets if a["status"] == "failed"]
check("no failed assets", len(failed) == 0, f"failed={[a['filename'] for a in failed][:5]}")

# 3) no-audio video reached searchable (the silent song clips)
videos = [a for a in assets if a["type"] == "video"]
if videos:
    vid_ok = all(a["status"] == "searchable" for a in videos)
    check("silent video clips searchable (no crash on 0 audio)", vid_ok,
          f"{sum(a['status']=='searchable' for a in videos)}/{len(videos)}")

# 4) reprocess idempotency — counts must not grow
img = next((a for a in assets if a["type"] == "image"), None)
if img:
    before = {c: vec_count(c, img["id"]) for c in ("dam_image", "dam_text", "dam_face")}
    s.post(f"{API}/api/assets/{img['id']}/reprocess", headers=H)
    for _ in range(40):
        st = s.get(f"{API}/api/assets/{img['id']}", headers=H).json()["status"]
        if st in ("searchable", "failed"):
            break
        time.sleep(5)
    time.sleep(2)
    after = {c: vec_count(c, img["id"]) for c in ("dam_image", "dam_text", "dam_face")}
    check("reprocess idempotent (image vectors stable)", before["dam_image"] == after["dam_image"],
          f"{before['dam_image']} -> {after['dam_image']}")
    check("reprocess idempotent (text vectors stable)", before["dam_text"] == after["dam_text"],
          f"{before['dam_text']} -> {after['dam_text']}")
    check("reprocess idempotent (no orphan growth)", after["dam_image"] <= before["dam_image"] + 1,
          f"img {before['dam_image']}->{after['dam_image']}")

# 5) every searchable asset has at least one searchable signal (vector or doc)
imgs = [a for a in assets if a["type"] == "image" and a["status"] == "searchable"][:5]
for a in imgs:
    n = vec_count("dam_image", a["id"])
    check(f"image '{a['filename'][:24]}' has image vector(s)", n >= 1, f"n={n}")

rep.write(f"\nSUMMARY: PASS={passes} FAIL={fails}\n")
with open(r"E:\dam-platform\.data\qa_ingest_report.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print(rep.getvalue())
