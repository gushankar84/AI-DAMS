r"""Test audio dialogue search: native-script phrase, English keyword (code-switched),
cross-lingual (English query -> native transcript), + frame-accurate timeline."""
import io

import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=60)
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

# (label, query, types, expect filename substring)
TESTS = [
    ("EN keyword in Hindi clip", "library application form", ["audio"], "rashmika"),
    ("semantic (books/stories)", "someone who loves reading books and stories", ["audio"], "rashmika"),
    ("native Tamil phrase", "நிறுத்துங்கள் வெட்டுங்கள்", ["audio"], "prakashraj"),
    ("x-lingual EN->Tamil", "stop it, cut", ["audio"], "prakashraj"),
    ("x-lingual EN->Arabic", "afraid to bring my children to the shelter", ["audio"], "hoda"),
    ("x-lingual EN->Arabic 2", "orphanage rent money", ["audio"], "hoda"),
    ("all-types (no filter)", "library application form", None, "rashmika"),
]

rep = io.StringIO()
p = f = 0
for label, q, types, expect in TESTS:
    body = {"q": q, "limit": 5}
    if types:
        body["types"] = types
    r = s.post(f"{API}/api/search", headers=H, json=body).json()
    hits = r["hits"]
    top = hits[0] if hits else None
    found_rank = next((i + 1 for i, h in enumerate(hits) if expect in h["filename"]), None)
    ok = found_rank is not None
    p += ok; f += (not ok)
    rep.write(f"[{'PASS' if ok else 'FAIL'}] {label:26s} '{q[:34]:34s}' -> n={r['total']} rank={found_rank}\n")
    if top:
        tl = top.get("timeline") or []
        sig = top.get("matched_signals")
        rep.write(f"        top={top['filename'][:30]} signals={sig} timeline={len(tl)}")
        if tl:
            rep.write(f" first@frame={tl[0].get('frame_index')} smpte={tl[0].get('smpte')}")
        rep.write(f"\n        snippet: {(top.get('snippet') or '')[:90]}\n")

rep.write(f"\nSUMMARY: PASS={p} FAIL={f}\n")
with open(r"E:\dam-platform\.data\qa_audio_search_report.txt", "w", encoding="utf-8") as fh:
    fh.write(rep.getvalue())
print(rep.getvalue())
