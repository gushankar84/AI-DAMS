r"""P3 image-AI E2E: upload photos -> faces (clustered to persons) + objects.

Run with the API venv:  apps\api\.venv\Scripts\python scripts\e2e_faces.py
"""
import os
import time
from collections import Counter

import httpx

API = os.environ.get("API_URL", "http://127.0.0.1:8000")
IMAGES = [
    r"C:\Users\PCPL\Downloads\Characters\Raghupathy1.jpg",
    r"C:\Users\PCPL\Downloads\Characters\Raghupathy2.jpg",
    r"C:\Users\PCPL\Downloads\Characters\Raghupathy3.jpg",
    r"C:\Users\PCPL\Downloads\Characters\Stella1.jpg",
    r"C:\Users\PCPL\Downloads\Characters\Stella2.jpg",
    r"C:\Users\PCPL\Downloads\ISRO-20260303T052510Z-3-001\ISRO\Render1.jpeg",
]


def main():
    c = httpx.Client(base_url=API, timeout=600)
    tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
                 headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    ids = {}
    for p in IMAGES:
        if not os.path.exists(p):
            print("skip missing", p); continue
        with open(p, "rb") as f:
            r = c.post("/api/assets", headers=h, files={"file": (os.path.basename(p), f)}).json()
        ids[r["asset_id"]] = os.path.basename(p)

    for _ in range(180):
        st = [c.get(f"/api/assets/{a}", headers=h).json()["status"] for a in ids]
        if all(s in ("searchable", "failed") for s in st):
            break
        time.sleep(3)

    persons_by_name = {}
    for aid, name in ids.items():
        d = c.get(f"/api/assets/{aid}", headers=h).json()
        faces = [m for m in d["markers"] if m["kind"] == "face"]
        objs = [m["label"] for m in d["markers"] if m["kind"] == "object"]
        pids = [m["person_id"] for m in faces]
        persons_by_name[name] = pids
        print(f"{name:18} status={d['status']:10} faces={len(faces)} persons={set(pids)} objects={Counter(objs)}")

    # Clustering check: same character's photos should share a person_id.
    print("\nperson_id sets per character prefix:")
    groups = {}
    for name, pids in persons_by_name.items():
        prefix = "".join([ch for ch in name if not ch.isdigit()]).split(".")[0]
        groups.setdefault(prefix, set()).update(pids)
    for prefix, pset in groups.items():
        print(f"  {prefix}: {pset}")


if __name__ == "__main__":
    main()
