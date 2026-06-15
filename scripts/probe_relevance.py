r"""Diagnose relevance: for a query, show each candidate's fused score, the
cross-encoder RERANK score, signals, and snippet — to see whether the reranker
already 'understands' the discriminating term (saree) or needs help."""
import io

import httpx

s = httpx.Client(timeout=60)
SRV = "http://127.0.0.1:8100"; API = "http://127.0.0.1:8000"
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

rep = io.StringIO()


def diag(q, types=None):
    body = {"q": q, "limit": 8, "rerank": True}
    if types:
        body["types"] = types
    r = s.post(f"{API}/api/search", headers=H, json=body).json()
    hits = r["hits"]
    # recompute the reranker score for each hit's passage (query vs title+snippet)
    passages = [f"{(h.get('title') or h['filename'])}. {h.get('snippet') or ''}".strip() for h in hits]
    rr = s.post(f"{SRV}/rerank", json={"query": q, "passages": passages}).json().get("scores", [])
    rep.write(f"\n=== {q!r}  ({r['total']} results) ===\n")
    for i, h in enumerate(hits):
        sc = rr[i] if i < len(rr) else None
        rep.write(f"  rerank={sc:.3f}  fused={h['score']:.4f}  [{h['type']}] {h['filename'][:30]:30} {h['matched_signals']}\n"
                  if sc is not None else f"  rerank=?  {h['filename']}\n")
        rep.write(f"        snippet: {(h.get('snippet') or '')[:70]!r}\n")


diag("a woman wearing a saree", ["video"])
diag("hindi song")
diag("a man in a blue shirt")  # known-good control

with open(r"E:\dam-platform\.data\relevance_probe.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print("written")
