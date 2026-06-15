r"""Calibrate noise floors: show raw top scores per signal for positive vs
negative queries, and the API signals driving negative hits."""
import io

import httpx

s = httpx.Client(timeout=60)
SRV = "http://127.0.0.1:8100"
API = "http://127.0.0.1:8000"
tok = s.post(f"{API}/api/auth/login",
             data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

QUERIES = [
    ("POS", "red dress"), ("POS", "blue shirt"), ("POS", "couple at sunset"),
    ("POS", "a man with a beard"),
    ("NEG", "a spaceship orbiting Saturn"), ("NEG", "asdfqwerzxcv"),
    ("NEG", "underwater coral reef scuba diving"), ("NEG", "quantum chromodynamics lecture"),
]


def top_scores(coll, vec, k=3):
    r = s.post(f"http://localhost:6333/collections/{coll}/points/query",
               json={"query": vec, "limit": k, "with_payload": False}).json()
    return [round(p["score"], 4) for p in r["result"]["points"]]


rep = io.StringIO()
rep.write(f"{'kind':4} {'query':32} {'img_top3 (SigLIP)':28} {'txt_top3 (BGE-M3)':24} api_signals\n")
for kind, q in QUERIES:
    iv = s.post(f"{SRV}/embed/clip-text", json={"text": q}).json()["vector"]
    tv = s.post(f"{SRV}/embed/text", json={"text": q}).json().get("vector")
    img = top_scores("dam_image", iv)
    txt = top_scores("dam_text", tv) if tv else []
    res = s.post(f"{API}/api/search", headers=H, json={"q": q, "limit": 5}).json()
    sigs = {}
    for h in res["hits"]:
        for sg in h["matched_signals"]:
            sigs[sg] = sigs.get(sg, 0) + 1
    rep.write(f"{kind:4} {q[:32]:32} {str(img):28} {str(txt):24} n={res['total']} {dict(sigs)}\n")

with open(r"E:\dam-platform\.data\qa_precision_report.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print(rep.getvalue())
