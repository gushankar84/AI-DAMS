r"""Backfill OCR across all existing images: reprocess each through the (now
OCR-aware) image pipeline so on-image text is extracted + indexed. Idempotent."""
import time

import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=180)
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

imgs = s.get(f"{API}/api/assets?type=image&limit=200", headers=H).json()
# skip the two already reprocessed in the verification step
ids = [a["id"] for a in imgs if not a["filename"].startswith("Frontal")]
for i in ids:
    s.post(f"{API}/api/assets/{i}/reprocess", headers=H)
print(f"reprocessing {len(ids)} images for OCR...")
for _ in range(360):
    sts = [s.get(f"{API}/api/assets/{i}", headers=H).json()["status"] for i in ids]
    if all(x in ("searchable", "failed") for x in sts):
        break
    time.sleep(6)
with_text = 0
for i in ids:
    d = s.get(f"{API}/api/assets/{i}", headers=H).json()
    if any(m.get("kind") == "ocr" for m in d.get("markers", [])):
        with_text += 1
        ocr = next(m["label"] for m in d["markers"] if m.get("kind") == "ocr")
        print(f"  TEXT  {d['filename'][:30]:30} {ocr[:50]!r}")
print(f"\ndone. {with_text}/{len(ids)} images had on-image text extracted")
