r"""Search VIDEOS by OBJECT (YOLO) and PEOPLE/SCENE (faces + VLM captions),
not just dialogue. Confirms the visual signals on video are searchable."""
import io

import httpx

s = httpx.Client(timeout=60)
API = "http://127.0.0.1:8000"
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

TESTS = [
    # ── objects (YOLO labels on shots) ──
    ("object", "a cow", "Bheegey Honth Tere_slp00001800"),
    ("object", "an umbrella on a beach", "Bheegey Honth Tere_slp00003600"),
    ("object", "a truck", "hit_tamil"),
    ("object", "a smartphone showing a call screen", "hit_tamil"),
    # ── people / scene (faces + VLM captions) ──
    ("scene", "a woman wearing a saree", "dasara"),
    ("scene", "people walking on a street at night", "dasara"),
    ("scene", "a man in a military uniform", "hit_tamil"),
    ("scene", "two women standing on a stage", "Pacarku"),
    ("people", "a man wearing a yellow kurta", "dasara_tamil"),
]

rep = io.StringIO()
p = f = 0
for cat, q, expect in TESTS:
    r = s.post(f"{API}/api/search", headers=H, json={"q": q, "types": ["video"], "limit": 5}).json()
    hits = r["hits"]
    rank = next((i + 1 for i, h in enumerate(hits) if expect in h["filename"]), None)
    ok = rank is not None
    p += ok; f += (not ok)
    top = hits[0]["filename"][:34] if hits else "(none)"
    sig = hits[0].get("matched_signals") if hits else []
    rep.write(f"[{'PASS' if ok else 'FAIL'}] ({cat:6}) '{q[:34]:34s}' -> n={r['total']} rank={rank}  top={top} {sig}\n")

rep.write(f"\nSUMMARY: PASS={p} FAIL={f}\n")
with open(r"E:\dam-platform\.data\qa_video_objpeople_report.txt", "w", encoding="utf-8") as fh:
    fh.write(rep.getvalue())
print(rep.getvalue())
