r"""Latency breakdown probe. Isolates query-embedding vs rerank vs fetch, and
measures the effect of GPU contention from a loaded VLM."""
import statistics
import time

import httpx

SRV = "http://127.0.0.1:8100"
API = "http://127.0.0.1:8000"


def med(fn, n=5):
    xs = []
    for _ in range(n):
        t0 = time.perf_counter()
        fn()
        xs.append((time.perf_counter() - t0) * 1000)
    return statistics.median(xs)


s = httpx.Client(timeout=60)
# direct model-server timings
t_text = med(lambda: s.post(f"{SRV}/embed/texts", json={"texts": ["a man in a blue shirt"]}))
t_clip = med(lambda: s.post(f"{SRV}/embed/clip-text", json={"text": "a man in a blue shirt"}))

# full search timings, rerank on vs off
tok = s.post(f"{API}/api/auth/login",
             data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}


def search(q, rerank, types=None):
    body = {"q": q, "limit": 5, "rerank": rerank}
    if types:
        body["types"] = types
    s.post(f"{API}/api/search", headers=H, json=body)


t_img_rr = med(lambda: search("a man in a blue shirt", True, ["image"]))
t_img_norr = med(lambda: search("a man in a blue shirt", False, ["image"]))
t_doc = med(lambda: search("media asset management architecture", True, ["document"]))
t_all = med(lambda: search("a person outdoors", True, None))

with open(r"E:\dam-platform\.data\qa_latency_report.txt", "w", encoding="utf-8") as f:
    f.write("=== LATENCY BREAKDOWN (median ms) ===\n")
    f.write(f"/embed/texts   (BGE-M3)  : {t_text:.0f}\n")
    f.write(f"/embed/clip-text (SigLIP): {t_clip:.0f}\n")
    f.write(f"search image  rerank=ON  : {t_img_rr:.0f}\n")
    f.write(f"search image  rerank=OFF : {t_img_norr:.0f}\n")
    f.write(f"search doc    rerank=ON  : {t_doc:.0f}\n")
    f.write(f"search all    rerank=ON  : {t_all:.0f}\n")
print(f"text={t_text:.0f} clip={t_clip:.0f} img_rr={t_img_rr:.0f} img_norr={t_img_norr:.0f} doc={t_doc:.0f} all={t_all:.0f}")
