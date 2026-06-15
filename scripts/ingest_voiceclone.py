r"""Ingest the Voice Clone test clips, wait for ASR, dump transcripts (UTF-8)."""
import io
import os
import time

import httpx

API = "http://127.0.0.1:8000"
CLIPS = r"E:\dam-platform\.data\voiceclone_clips"
c = httpx.Client(base_url=API, timeout=600)
tok = c.post("/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

ids = {}
for fn in sorted(os.listdir(CLIPS)):
    p = os.path.join(CLIPS, fn)
    with open(p, "rb") as f:
        r = c.post("/api/assets", headers=H, files={"file": (fn, f)}).json()
    ids[r["asset_id"]] = fn
    print(f"queued {fn} [{r.get('status')}]")

print(f"\nwaiting for {len(ids)} clips to index (ASR)...")
for _ in range(450):
    sts = {a: c.get(f"/api/assets/{a}", headers=H).json()["status"] for a in ids}
    pending = [s for s in sts.values() if s not in ("searchable", "failed")]
    if not pending:
        break
    time.sleep(4)

rep = io.StringIO()
for a, fn in ids.items():
    d = c.get(f"/api/assets/{a}", headers=H).json()
    tr = d.get("transcript", []) or []
    text = " ".join((seg.get("text") or "").strip() for seg in tr)
    rep.write(f"\n=== {fn}  [{d['status']}]  type={d['type']}  lang={d.get('language')}  segments={len(tr)} ===\n")
    rep.write(f"id={a}\n")
    rep.write(f"transcript ({len(text)} chars): {text[:500]}\n")
    if tr:
        s0 = tr[0]
        rep.write(f"first segment: start={s0.get('start_frame')} smpte={s0.get('smpte')} text={s0.get('text','')[:80]}\n")

with open(r"E:\dam-platform\.data\voiceclone_transcripts.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print("transcripts written")
