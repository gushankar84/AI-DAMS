r"""Production QA — concurrency & scalability. Fire many concurrent searches and
measure latency distribution + success rate. Surfaces event-loop serialization."""
import statistics
import time
from concurrent.futures import ThreadPoolExecutor

import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=60)
tok = s.post(f"{API}/api/auth/login",
             data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
QUERIES = ["blue shirt", "red dress", "couple at sunset", "a man with a beard",
           "green saree", "media asset management", "people on a stage", "eyeglasses"]


def one(i):
    c = httpx.Client(timeout=60)
    q = QUERIES[i % len(QUERIES)]
    t0 = time.perf_counter()
    r = c.post(f"{API}/api/search", headers=H, json={"q": q, "limit": 5})
    dt = (time.perf_counter() - t0) * 1000
    return r.status_code, dt


for N in (1, 8, 24, 48):
    t0 = time.perf_counter()
    with ThreadPoolExecutor(max_workers=N) as ex:
        results = list(ex.map(one, range(N)))
    wall = (time.perf_counter() - t0) * 1000
    codes = [c for c, _ in results]
    lats = [d for _, d in results]
    ok = sum(c == 200 for c in codes)
    print(f"N={N:3d}  ok={ok}/{N}  wall={wall:6.0f}ms  p50={statistics.median(lats):5.0f}  "
          f"p95={sorted(lats)[max(0,int(len(lats)*0.95)-1)]:5.0f}  max={max(lats):5.0f}  "
          f"throughput={N/(wall/1000):.1f} req/s")
