r"""End-to-end smoke test: login -> upload -> wait for indexing -> semantic search.

Usage:  python scripts/e2e.py "<path-to-file>" "<search query>"
Run with the API venv (has httpx):  apps\api\.venv\Scripts\python scripts\e2e.py ...
"""
import os
import sys
import time

import httpx

API = os.environ.get("API_URL", "http://127.0.0.1:8000")


def main(path: str, query: str):
    c = httpx.Client(base_url=API, timeout=120)

    # 1. login
    r = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
               headers={"Content-Type": "application/x-www-form-urlencoded"})
    r.raise_for_status()
    tok = r.json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    print("login ok")

    # 2. upload
    fname = os.path.basename(path)
    with open(path, "rb") as f:
        r = c.post("/api/assets", headers=h, files={"file": (fname, f)})
    r.raise_for_status()
    asset_id = r.json()["asset_id"]
    print(f"uploaded {fname} -> asset {asset_id}")

    # 3. poll until searchable
    status = None
    for _ in range(120):
        r = c.get(f"/api/assets/{asset_id}", headers=h)
        status = r.json()["status"]
        if status in ("searchable", "failed"):
            break
        time.sleep(2)
    print(f"final status: {status}")
    if status == "failed":
        print("error_detail:", r.json().get("error_detail"))
        return

    # 4. semantic search
    r = c.post("/api/search", headers=h, json={"q": query, "limit": 5})
    r.raise_for_status()
    res = r.json()
    print(f"\nquery: {query!r}  ->  {res['total']} hits in {res['took_ms']}ms")
    for hit in res["hits"]:
        print(f"  [{hit['type']}] {hit['title']}  score={hit['score']}  signals={hit['matched_signals']}")
        if hit.get("snippet"):
            print(f"      …{hit['snippet'][:160]}…")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "facial recognition privacy and consent")
