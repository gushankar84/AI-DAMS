r"""Audio E2E: upload audio -> ASR -> transcript -> search by transcript content.

Run with the API venv:  apps\api\.venv\Scripts\python scripts\e2e_audio.py "<path-to-audio>"
"""
import os
import re
import sys
import time

import httpx

API = os.environ.get("API_URL", "http://127.0.0.1:8000")


def main(path: str):
    c = httpx.Client(base_url=API, timeout=600)
    tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
                 headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    fname = os.path.basename(path)
    with open(path, "rb") as f:
        aid = c.post("/api/assets", headers=h, files={"file": (fname, f)}).json()["asset_id"]
    print("uploaded", fname, "->", aid)

    detail = None
    for _ in range(180):
        detail = c.get(f"/api/assets/{aid}", headers=h).json()
        if detail["status"] in ("searchable", "failed"):
            break
        time.sleep(2)
    print("status:", detail["status"], "| transcript segments:", len(detail.get("transcript", [])))
    if detail["status"] == "failed":
        print("error:", detail.get("error_detail")); return

    for seg in detail["transcript"][:5]:
        ss = seg.get("start_seconds")
        clock = f"{int(ss//3600):02d}:{int(ss%3600//60):02d}:{int(ss%60):02d}" if ss is not None else "?"
        print(f"  [{clock}] {seg['text']}")

    # Build a search phrase from the transcript content words (language-agnostic).
    words = []
    for seg in detail["transcript"]:
        words += re.findall(r"\w{4,}", seg["text"])
    if not words:
        print("no transcript words to search"); return
    phrase = " ".join(words[:4])
    r = c.post("/api/search", headers=h, json={"q": phrase, "limit": 5}).json()
    print(f"\nsearch {phrase!r} -> {r['total']} hits ({r['took_ms']}ms)")
    for hit in r["hits"]:
        print(f"   [{hit['type']}] {hit['title']}  signals={hit['matched_signals']}")
        for tl in hit.get("timeline", [])[:3]:
            print(f"       @ {tl.get('smpte')}  {tl.get('snippet')}")


if __name__ == "__main__":
    main(sys.argv[1])
