r"""Ingest a few UHD song-video segments to validate the video pipeline on real content.
Run with the API venv. Prints transcript + detection summary per segment.
"""
import os
import time

import httpx

API = "http://127.0.0.1:8000"
BASE = r"C:\Users\PCPL\Downloads\wetransfer_1-bheegey-honth-tere_uhd_2026-06-03_1221\1.Bheegey Honth Tere_UHD"
SEGMENTS = [
    "1.Bheegey Honth Tere_slp00000000.mp4",
    "1.Bheegey Honth Tere_slp00001800.mp4",
    "1.Bheegey Honth Tere_slp00003600.mp4",
]


def main():
    c = httpx.Client(base_url=API, timeout=1800)
    tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
                 headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    ids = []
    for s in SEGMENTS:
        p = os.path.join(BASE, s)
        if not os.path.exists(p):
            print("missing:", p); continue
        with open(p, "rb") as f:
            r = c.post("/api/assets", headers=h, files={"file": (s, f)}).json()
        ids.append(r["asset_id"]); print("uploaded", s, "->", r["asset_id"])

    print("waiting for video pipeline (shots + ASR + faces/objects)…")
    for _ in range(600):
        sts = [c.get(f"/api/assets/{a}", headers=h).json()["status"] for a in ids]
        if all(s in ("searchable", "failed") for s in sts):
            break
        time.sleep(5)

    for a in ids:
        d = c.get(f"/api/assets/{a}", headers=h).json()
        kinds = {}
        for m in d["markers"]:
            kinds[m["kind"]] = kinds.get(m["kind"], 0) + 1
        print(f"\n{d['filename']}  [{d['status']}]")
        print(f"  markers: {kinds}  | transcript segments: {len(d['transcript'])}")
        for t in d["transcript"][:4]:
            print(f"    speech @ {t.get('start_seconds')}s: {t['text'][:70]}")


if __name__ == "__main__":
    main()
