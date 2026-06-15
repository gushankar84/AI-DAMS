r"""Wait for the 3 Voice Clone video clips to finish, then dump their transcripts."""
import io
import time

import httpx

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=60)
tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

vids = [a for a in c.get("/api/assets?limit=200", headers=H).json() if a["filename"].endswith("_video.mp4")]
for _ in range(300):  # up to ~20 min
    sts = [c.get(f"/api/assets/{a['id']}", headers=H).json()["status"] for a in vids]
    if all(s in ("searchable", "failed") for s in sts):
        break
    time.sleep(5)

rep = io.StringIO()
for a in vids:
    d = c.get(f"/api/assets/{a['id']}", headers=H).json()
    tr = d.get("transcript", []) or []
    text = " ".join((seg.get("text") or "").strip() for seg in tr)
    rep.write(f"=== {a['filename']} [{d['status']}] lang={d.get('language')} segs={len(tr)} ===\n{text[:350]}\n\n")
with open(r"E:\dam-platform\.data\voiceclone_video_transcripts.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print("video transcripts written")
