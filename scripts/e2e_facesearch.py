r"""Face-search E2E: upload a query face image, find matching assets.

Run with the API venv:  apps\api\.venv\Scripts\python scripts\e2e_facesearch.py "<face.jpg>"
"""
import os
import sys

import httpx

API = os.environ.get("API_URL", "http://127.0.0.1:8000")


def main(path: str):
    c = httpx.Client(base_url=API, timeout=300)
    tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
                 headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}
    with open(path, "rb") as f:
        r = c.post("/api/search/face", headers=h, files={"file": (os.path.basename(path), f)}).json()
    print(f"query face: {os.path.basename(path)} -> {r['total']} matching assets ({r['took_ms']}ms)")
    for hit in r["hits"]:
        print(f"   [{hit['type']}] {hit['filename']:20}  cosine={hit['score']}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else r"C:\Users\PCPL\Downloads\Characters\Raghupathy2.jpg")
