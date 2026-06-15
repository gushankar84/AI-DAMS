r"""Video E2E: upload a clip -> shots + faces/objects + ASR (frame-mapped) -> search.

Run with the API venv:  apps\api\.venv\Scripts\python scripts\e2e_video.py "<clip.mp4>"
"""
import os
import sys
import time
from collections import Counter

import httpx

API = os.environ.get("API_URL", "http://127.0.0.1:8000")


def main(path: str):
    c = httpx.Client(base_url=API, timeout=900)
    tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
                 headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    with open(path, "rb") as f:
        aid = c.post("/api/assets", headers=h, files={"file": (os.path.basename(path), f)}).json()["asset_id"]
    print("uploaded", os.path.basename(path), "->", aid)

    d = None
    for _ in range(200):
        d = c.get(f"/api/assets/{aid}", headers=h).json()
        if d["status"] in ("searchable", "failed"):
            break
        time.sleep(3)
    print("status:", d["status"])
    if d["status"] == "failed":
        print("error:", d.get("error_detail")); return

    kinds = Counter(m["kind"] for m in d["markers"])
    labels = Counter(m["label"] for m in d["markers"] if m["kind"] == "object")
    print(f"markers: {dict(kinds)}")
    print(f"object labels: {dict(labels)}")
    print(f"transcript segments: {len(d['transcript'])}")
    for m in [m for m in d["markers"] if m["kind"] == "shot"][:5]:
        print(f"   shot @ {m['smpte']} (frame {m['frame_index']}-{m['end_frame']})")
    for seg in d["transcript"][:4]:
        print(f"   speech @ {seg.get('start_seconds')}s: {seg['text'][:80]}")


if __name__ == "__main__":
    main(sys.argv[1])
