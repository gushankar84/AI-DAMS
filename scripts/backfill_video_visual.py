r"""Backfill: embed existing video keyframes into dam_image so video becomes
findable by appearance (visual search). Reconstructs keyframe keys from the shot
markers (frame_index) — no re-caption needed. Deterministic point ids => idempotent."""
import uuid

import httpx

API = "http://127.0.0.1:8000"
SRV = "http://127.0.0.1:8100"
QD = "http://localhost:6333"
BUCKET = "dam-assets"
NS = uuid.UUID("a1b2c3d4-0000-4000-8000-000000000001")

s = httpx.Client(timeout=180)
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

vids = s.get(f"{API}/api/assets?type=video&limit=50", headers=H).json()
total = 0
for v in vids:
    aid = v["id"]
    d = s.get(f"{API}/api/assets/{aid}", headers=H).json()
    shots = [(m["frame_index"], m.get("smpte")) for m in d.get("markers", [])
             if m.get("kind") == "shot" and m.get("frame_index") is not None]
    pts = []
    for fr, smpte in shots:
        uri = f"s3://{BUCKET}/video/{aid}/keyframes/{fr}.jpg"
        try:
            vec = s.post(f"{SRV}/embed/image", json={"storage_uri": uri, "filename": f"{fr}.jpg"}).json()["vector"]
        except Exception as e:
            print(f"  embed fail {aid[:8]} frame {fr}: {e}")
            continue
        pid = str(uuid.uuid5(NS, f"{aid}:{fr}:frame"))
        pts.append({"id": pid, "vector": vec, "payload": {
            "asset_id": aid, "asset_type": "video", "region": "frame",
            "frame_index": fr, "smpte": smpte, "frame_uri": uri,
            "department": v.get("department"), "project": v.get("project")}})
    if pts:
        r = s.put(f"{QD}/collections/dam_image/points?wait=true", json={"points": pts})
        total += len(pts)
        print(f"  {v['filename'][:36]:36} +{len(pts)} keyframes (qdrant {r.status_code})")
    else:
        print(f"  {v['filename'][:36]:36} (no shots)")
print(f"\nbackfilled {total} video keyframes into dam_image")
