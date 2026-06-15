r"""Probe cross-lingual dam_text scores for the failing query vs working variants."""
import io

import httpx

s = httpx.Client(timeout=30)
SRV = "http://127.0.0.1:8100"; API = "http://127.0.0.1:8000"
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
hoda = [a for a in s.get(f"{API}/api/assets?type=audio&limit=20", headers=H).json() if "hoda" in a["filename"]][0]["id"]

rep = io.StringIO()


def probe(q):
    v = s.post(f"{SRV}/embed/text", json={"text": q}).json()["vector"]
    r = s.post("http://localhost:6333/collections/dam_text/points/query",
               json={"query": v, "limit": 30, "with_payload": True}).json()["result"]["points"]
    hoda_best = max((p["score"] for p in r if p["payload"].get("asset_id") == hoda), default=None)
    top = r[0]
    rep.write(f"\nQ: {q!r}  (threshold=0.45)\n")
    rep.write(f"   top overall: {top['score']:.3f}  asset={top['payload'].get('asset_id','')[:8]}\n")
    rep.write(f"   HODA best:   {hoda_best:.3f}\n" if hoda_best is not None else "   HODA: not in top 30\n")


for q in ["orphanage rent money",
          "afraid to bring my children to the shelter",
          "a woman who lost her home and children",
          "shelter refuge children rent"]:
    probe(q)

with open(r"E:\dam-platform\.data\xling_probe.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print("probe written")
