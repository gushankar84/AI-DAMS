r"""Test video dialogue search: keyword (code-switched), native script, cross-lingual,
type filter, + frame-accurate shot/transcript timeline."""
import io

import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=60)
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

TESTS = [
    ("EN tech words in Tamil clip", "5G internet buffering", ["video"], "hit_tamil"),
    ("semantic (streaming video)", "watching a video on YouTube keeps buffering", ["video"], "hit_tamil"),
    ("native Tamil name", "ரக்ஷிதா", ["video"], "hit_tamil"),
    ("x-lingual EN->marriage", "they are getting married, a wedding", ["video"], "dasara"),
    ("all-types (no filter)", "5G buffering YouTube", None, "hit_tamil"),
]

rep = io.StringIO()
p = f = 0
for label, q, types, expect in TESTS:
    body = {"q": q, "limit": 5}
    if types:
        body["types"] = types
    r = s.post(f"{API}/api/search", headers=H, json=body).json()
    hits = r["hits"]
    rank = next((i + 1 for i, h in enumerate(hits) if expect in h["filename"]), None)
    ok = rank is not None
    p += ok; f += (not ok)
    rep.write(f"[{'PASS' if ok else 'FAIL'}] {label:28s} '{q[:30]:30s}' -> n={r['total']} rank={rank}\n")
    top = hits[0] if hits else None
    if top:
        tl = top.get("timeline") or []
        rep.write(f"        top={top['filename'][:26]} signals={top.get('matched_signals')} timeline={len(tl)}")
        if tl:
            rep.write(f" @frame={tl[0].get('frame_index')} smpte={tl[0].get('smpte')} kind={tl[0].get('kind')}")
        rep.write("\n")

rep.write(f"\nSUMMARY: PASS={p} FAIL={f}\n")
with open(r"E:\dam-platform\.data\qa_video_search_report.txt", "w", encoding="utf-8") as fh:
    fh.write(rep.getvalue())
print(rep.getvalue().encode("ascii", "replace").decode())
