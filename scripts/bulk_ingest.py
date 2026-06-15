r"""Bulk-ingest a curated, varied image set for an accuracy test.
Sensitive files (Aadhaar/cheque/policy) and mask frames are deliberately excluded.

Run:  apps\api\.venv\Scripts\python scripts\bulk_ingest.py
"""
import os
import time

import httpx

API = "http://127.0.0.1:8000"
DL = r"C:\Users\PCPL\Downloads"

FILES = [
    # People (characters)
    r"Characters\Raghupathy1.jpg", r"Characters\Raghupathy2.jpg", r"Characters\Raghupathy3.jpg",
    r"Characters\Stella1.jpg", r"Characters\Stella2.jpg", r"Characters\Stella3.jpg",
    r"Characters\Jagan 1.jpg", r"Characters\Jagan 2.jpg", r"Characters\Jagan 3.jpg",
    # Renders / buildings / aerial
    r"ISRO-20260303T052510Z-3-001\ISRO\Render1.jpeg", r"ISRO-20260303T052510Z-3-001\ISRO\Render2.jpeg",
    r"ISRO-20260303T052510Z-3-001\ISRO\Render3.jpeg", r"ISRO-20260303T052510Z-3-001\ISRO\Frontal aerial.png",
    r"ISRO-20260303T052510Z-3-001\ISRO\Frontal.png",
    # Logo
    r"sqlite-src-3510100\sqlite-src-3510100\art\sqlite370.jpg",
    # AI-generated art
    r"ChatGPT Image Jan 11, 2026, 02_00_45 PM.png", r"ChatGPT Image Jan 11, 2026, 02_10_46 PM.png",
    # Misc WhatsApp photos (varied content)
    r"WhatsApp Image 2025-12-18 at 6.00.20 PM.jpeg",
    r"WhatsApp Image 2026-01-16 at 5.38.46 PM.jpeg", r"WhatsApp Image 2026-01-16 at 5.38.46 PM (1).jpeg",
    r"WhatsApp Image 2026-01-30 at 4.40.33 PM.jpeg", r"WhatsApp Image 2026-01-30 at 4.40.34 PM.jpeg",
    r"WhatsApp Image 2026-01-30 at 4.40.35 PM.jpeg",
    r"WhatsApp Image 2026-05-31 at 6.56.44 PM.jpeg", r"WhatsApp Image 2026-06-08 at 2.18.30 PM.jpeg",
    # A few scanned frames for variety
    r"wetransfer_negative-scan_2026-01-02_0652\JPL\Book_01\Book_01_001.jpg",
    r"wetransfer_negative-scan_2026-01-02_0652\JPL\Book_01\Book_01_005.jpg",
    r"wetransfer_negative-scan_2026-01-02_0652\JPL\Book_02\Book_02_003.jpg",
    r"wetransfer_negative-scan_2026-01-02_0652\JPL\Book_02\Book_02_008.jpg",
]


def main():
    c = httpx.Client(base_url=API, timeout=120)
    tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
                 headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
    h = {"Authorization": f"Bearer {tok}"}

    ids = []
    for rel in FILES:
        p = os.path.join(DL, rel)
        if not os.path.exists(p):
            print("  skip (missing):", rel); continue
        with open(p, "rb") as f:
            r = c.post("/api/assets", headers=h, files={"file": (os.path.basename(p), f)}).json()
        ids.append(r["asset_id"])
        print(f"  queued {os.path.basename(p)} [{r['status']}]")

    print(f"\nuploaded/queued {len(ids)} assets; waiting for indexing…")
    for _ in range(300):
        sts = [c.get(f"/api/assets/{a}", headers=h).json()["status"] for a in ids]
        pending = [s for s in sts if s not in ("searchable", "failed")]
        if not pending:
            break
        time.sleep(3)
    final = {}
    for a in ids:
        s = c.get(f"/api/assets/{a}", headers=h).json()["status"]
        final[s] = final.get(s, 0) + 1
    print("final statuses:", final)
    tot = c.post("/api/search", headers=h, json={"q": "", "limit": 1}).json()["total"]
    print("total searchable in index now:", tot)


if __name__ == "__main__":
    main()
