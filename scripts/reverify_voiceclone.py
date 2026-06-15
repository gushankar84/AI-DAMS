r"""Reprocess the Voice Clone clips (audio first), confirm ASR transcribes, dump text."""
import io
import sys
import time

import httpx

API = "http://127.0.0.1:8000"
c = httpx.Client(base_url=API, timeout=600)
tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

kind = sys.argv[1] if len(sys.argv) > 1 else "audio"
clips = [a for a in c.get("/api/assets?limit=200", headers=H).json()
         if a["filename"].endswith(f"_{kind}.mp3") or a["filename"].endswith(f"_{kind}.wav")
         or a["filename"].endswith(f"_{kind}.mp4")]

for a in clips:
    c.post(f"/api/assets/{a['id']}/reprocess", headers=H)
print(f"reprocessing {len(clips)} {kind} clips...")
for _ in range(300):
    sts = [c.get(f"/api/assets/{a['id']}", headers=H).json()["status"] for a in clips]
    if all(s in ("searchable", "failed") for s in sts):
        break
    time.sleep(4)

rep = io.StringIO()
for a in clips:
    d = c.get(f"/api/assets/{a['id']}", headers=H).json()
    tr = d.get("transcript", []) or []
    text = " ".join((seg.get("text") or "").strip() for seg in tr)
    rep.write(f"\n=== {a['filename']}  [{d['status']}]  lang={d.get('language')}  segs={len(tr)} ===\n")
    rep.write(f"id={a['id']}\n{text[:600]}\n")
    if tr:
        s0 = tr[0]
        rep.write(f"  first hit: frame={s0.get('start_frame')} smpte={s0.get('smpte')}\n")
with open(rf"E:\dam-platform\.data\voiceclone_{kind}_transcripts.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print(f"{kind} transcripts written")
