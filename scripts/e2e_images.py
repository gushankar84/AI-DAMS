r"""Image-path E2E: upload several images, wait for indexing, run text->image queries.

Run with the API venv:  apps\api\.venv\Scripts\python scripts\e2e_images.py
"""
import os
import time

import httpx

API = os.environ.get("API_URL", "http://127.0.0.1:8000")

IMAGES = [
    r"C:\Users\PCPL\Downloads\ISRO-20260303T052510Z-3-001\ISRO\Frontal aerial.png",
    r"C:\Users\PCPL\Downloads\ISRO-20260303T052510Z-3-001\ISRO\Render1.jpeg",
    r"C:\Users\PCPL\Downloads\sqlite-src-3510100\sqlite-src-3510100\art\sqlite370.jpg",
    r"C:\Users\PCPL\Downloads\Characters\Raghupathy1.jpg",
]
QUERIES = [
    "a photograph of a person's face",
    "an aerial architectural render of a building",
    "a software logo",
]


def main():
    c = httpx.Client(base_url=API, timeout=180)
    tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
                 headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    ids = {}
    for path in IMAGES:
        if not os.path.exists(path):
            print("skip (missing):", path); continue
        fname = os.path.basename(path)
        with open(path, "rb") as f:
            r = c.post("/api/assets", headers=h, files={"file": (fname, f)})
        r.raise_for_status()
        ids[r.json()["asset_id"]] = fname
        print("uploaded", fname)

    # wait for all searchable
    for _ in range(120):
        statuses = [c.get(f"/api/assets/{aid}", headers=h).json()["status"] for aid in ids]
        if all(s in ("searchable", "failed") for s in statuses):
            break
        time.sleep(2)
    print("statuses:", {ids[a]: c.get(f"/api/assets/{a}", headers=h).json()["status"] for a in ids})

    for q in QUERIES:
        r = c.post("/api/search", headers=h, json={"q": q, "types": ["image"], "limit": 4}).json()
        print(f"\nq: {q!r} -> {r['total']} hits ({r['took_ms']}ms)")
        for hit in r["hits"]:
            print(f"   {hit['title']:32}  score={hit['score']:.4f}  signals={hit['matched_signals']}")


if __name__ == "__main__":
    main()
