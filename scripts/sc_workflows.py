r"""Scenario harness — non-search workflows end-to-end."""
import io
import os

import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=120)
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
rep = io.StringIO()
p = f = 0


def check(name, cond, detail=""):
    global p, f
    ok = bool(cond); p += ok; f += (not ok)
    rep.write(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}\n")


def assets(**q):
    qs = "&".join(f"{k}={v}" for k, v in q.items())
    return s.get(f"{API}/api/assets?{qs}", headers=H).json()


# ── 1) FACE SEARCH + CONSENT GATING ──
face_img = r"C:\Users\PCPL\Downloads\Characters\Raghupathy1.jpg"
if os.path.exists(face_img):
    with open(face_img, "rb") as fh:
        r = s.post(f"{API}/api/search/face", headers=H, files={"file": ("q.jpg", fh, "image/jpeg")})
    fr = r.json() if r.status_code == 200 else {}
    hits = fr.get("hits", [])
    check("face search returns matches", r.status_code == 200 and len(hits) >= 1, f"-> {r.status_code} n={len(hits)}")
    # person id via the matched asset's face markers (image hits have no timeline frame)
    pid = None
    if hits:
        det = s.get(f"{API}/api/assets/{hits[0]['asset_id']}", headers=H).json()
        pid = next((m.get("person_id") for m in det.get("markers", [])
                    if m.get("kind") == "face" and m.get("person_id")), None)
    if pid:
        base_n = len(hits)
        s.patch(f"{API}/api/persons/{pid}", headers=H, json={"consent_status": "denied"})
        with open(face_img, "rb") as fh:
            r2 = s.post(f"{API}/api/search/face", headers=H, files={"file": ("q.jpg", fh, "image/jpeg")}).json()
        check("consent=denied excludes the person from face search", r2["total"] <= base_n - 1 or r2["total"] < base_n,
              f"{base_n} -> {r2['total']}")
        s.patch(f"{API}/api/persons/{pid}", headers=H, json={"consent_status": "unknown"})  # restore
    else:
        check("face hit carried a person_id", False, "no person_id in timeline")
else:
    check("face query image present", False, face_img)

# ── 2) COLLECTIONS CRUD ──
col = s.post(f"{API}/api/collections", headers=H, json={"name": "SC Test", "description": "scenario"}).json()
cid = col.get("id")
imgs = [a["id"] for a in assets(type="image", limit=3)][:2]
for aid in imgs:
    s.post(f"{API}/api/collections/{cid}/items/{aid}", headers=H)
got = s.get(f"{API}/api/collections/{cid}", headers=H).json()
check("collection add items", len(got.get("assets", [])) == 2, f"assets={len(got.get('assets', []))}")
s.delete(f"{API}/api/collections/{cid}/items/{imgs[0]}", headers=H)
got2 = s.get(f"{API}/api/collections/{cid}", headers=H).json()
check("collection remove item", len(got2.get("assets", [])) == 1, f"assets={len(got2.get('assets', []))}")

# ── 3) DISTRIBUTION share + expiry ──
aid = assets(type="image", limit=1)[0]["id"]
r = s.post(f"{API}/api/shares", headers=H, json={
    "scope_type": "asset", "scope_id": aid, "permission": "view",
    "expiry": "2030-01-01T00:00:00", "watermark": True})
sh = r.json() if r.status_code == 200 else {}
check("create share link", r.status_code == 200 and sh.get("token"), f"-> {r.status_code}")
check("share carries expiry + watermark", bool(sh.get("expiry")) and sh.get("watermark") is True, f"expiry={sh.get('expiry')} wm={sh.get('watermark')}")

# ── 4) ASSET DETAIL: transcript + frame-mapped timeline ──
aud = [a for a in assets(type="audio", limit=20) if "rashmika" in a["filename"]]
if aud:
    d = s.get(f"{API}/api/assets/{aud[0]['id']}", headers=H).json()
    tr = d.get("transcript", []) or []
    check("audio asset has transcript segments", len(tr) > 0, f"segs={len(tr)}")
    frames_ok = all((seg.get("start_frame") or 0) >= 0 for seg in tr)
    check("transcript frames are valid (>=0)", frames_ok, "")

# ── 5) TRASH: soft-delete excludes from search, then restore ──
timg = assets(type="image", limit=5)[-1]
tid, tname = timg["id"], timg["filename"]
s.delete(f"{API}/api/assets/{tid}", headers=H)
after = s.post(f"{API}/api/search", headers=H, json={"q": "", "limit": 200}).json()
present = any(h["asset_id"] == tid for h in after["hits"])
check("soft-deleted asset excluded from search", not present, f"still_present={present}")
# restore (PATCH deleted_at None if supported, else via a restore endpoint)
rstat = s.patch(f"{API}/api/assets/{tid}", headers=H, json={"deleted_at": None})
check("restore endpoint reachable (not 500)", rstat.status_code != 500, f"-> {rstat.status_code}")

# cleanup collection
if cid:
    try: s.delete(f"{API}/api/collections/{cid}", headers=H)
    except Exception: pass

rep.write(f"\nSUMMARY: PASS={p} FAIL={f}\n")
with open(r"E:\dam-platform\.data\sc_workflows_report.txt", "w", encoding="utf-8") as fh:
    fh.write(rep.getvalue())
print(rep.getvalue())
