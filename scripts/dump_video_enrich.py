r"""Dump what was detected in each video (objects, scene captions, faces) so we can
query videos by object/people/scene rather than just dialogue."""
import io

import httpx

s = httpx.Client(timeout=30)
API = "http://127.0.0.1:8000"
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

rep = io.StringIO()
vids = [a for a in s.get(f"{API}/api/assets?type=video&limit=50", headers=H).json()]
for a in vids:
    d = s.get(f"{API}/api/assets/{a['id']}", headers=H).json()
    ms = d.get("markers", [])
    objs = sorted({m.get("label") for m in ms if m.get("kind") == "object" and m.get("label")})
    faces = sum(1 for m in ms if m.get("kind") == "face")
    shots = sum(1 for m in ms if m.get("kind") == "shot")
    scenes = [m.get("label") for m in ms if m.get("kind") == "scene" and m.get("label")]
    rep.write(f"\n=== {a['filename']} ===\n")
    rep.write(f"  shots={shots} faces={faces} objects={objs}\n")
    for sc in scenes[:3]:
        rep.write(f"  scene: {sc[:140]}\n")

with open(r"E:\dam-platform\.data\video_enrich.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print(rep.getvalue().encode("ascii", "replace").decode())
