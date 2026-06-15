r"""Production QA — API contract, auth gating, input validation, error handling.
Checks that protected routes reject anon access, bad input -> 4xx not 500, missing
resources -> 404, pagination works, and response shapes are correct.
"""
import io

import httpx

API = "http://127.0.0.1:8000"
anon = httpx.Client(base_url=API, timeout=30)
tok = anon.post("/api/auth/login",
                data={"username": "admin@dam.local", "password": "admin12345"},
                headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

rep = io.StringIO()
passes = fails = 0


def check(name, cond, detail=""):
    global passes, fails
    ok = bool(cond)
    passes += ok; fails += (not ok)
    rep.write(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}\n")


# 1) Auth gating — protected endpoints must reject anon
for path, method in [("/api/search", "POST"), ("/api/assets", "GET"),
                     ("/api/stats", "GET"), ("/api/persons", "GET"), ("/api/collections", "GET")]:
    r = anon.request(method, path, json={} if method == "POST" else None)
    check(f"anon {method} {path} rejected", r.status_code in (401, 403), f"-> {r.status_code}")

# 2) Bad token rejected
r = anon.get("/api/assets", headers={"Authorization": "Bearer garbage.token.here"})
check("garbage token rejected", r.status_code in (401, 403), f"-> {r.status_code}")

# 3) Bad login rejected
r = anon.post("/api/auth/login", data={"username": "admin@dam.local", "password": "wrong"},
              headers={"Content-Type": "application/x-www-form-urlencoded"})
check("wrong password rejected", r.status_code in (400, 401), f"-> {r.status_code}")

# 4) Input validation -> 422, not 500
r = anon.post("/api/search", headers=H, json={"limit": "not-a-number"})
check("invalid search body -> 422", r.status_code == 422, f"-> {r.status_code}")
r = anon.post("/api/search", headers=H, json={"q": "x", "limit": -5})
check("negative limit handled (not 500)", r.status_code != 500, f"-> {r.status_code}")
r = anon.post("/api/search", headers=H, json={"q": "x", "limit": 100000})
check("huge limit handled (not 500)", r.status_code != 500, f"-> {r.status_code}")

# 5) Missing resource -> 404 not 500
r = anon.get("/api/assets/00000000-0000-0000-0000-000000000000", headers=H)
check("missing asset -> 404", r.status_code == 404, f"-> {r.status_code}")
r = anon.get("/api/assets/not-a-uuid", headers=H)
check("malformed id handled (not 500)", r.status_code in (404, 422), f"-> {r.status_code}")

# 6) Response shape
r = anon.post("/api/search", headers=H, json={"q": "blue shirt", "limit": 3})
j = r.json()
check("search response shape", all(k in j for k in ("query", "total", "took_ms", "hits")), str(list(j.keys())))
if j["hits"]:
    h = j["hits"][0]
    check("hit shape", all(k in h for k in ("asset_id", "type", "filename", "score", "matched_signals")), str(list(h.keys())))

# 7) Pagination consistency
r1 = anon.post("/api/search", headers=H, json={"q": "a person", "limit": 2, "offset": 0}).json()
r2 = anon.post("/api/search", headers=H, json={"q": "a person", "limit": 2, "offset": 2}).json()
ids1 = {h["asset_id"] for h in r1["hits"]}
ids2 = {h["asset_id"] for h in r2["hits"]}
check("pagination no overlap", ids1.isdisjoint(ids2), f"p1={len(ids1)} p2={len(ids2)} overlap={len(ids1 & ids2)}")

# 8) Filters honoured
r = anon.post("/api/search", headers=H, json={"q": "a", "types": ["document"], "limit": 10}).json()
types = {h["type"] for h in r["hits"]}
check("type filter honoured", types <= {"document"} or not r["hits"], f"types={types}")

# 9) Health endpoints
r = anon.get("/api/health")
check("health open (no auth)", r.status_code == 200, f"-> {r.status_code}")

rep.write(f"\nSUMMARY: PASS={passes} FAIL={fails}\n")
with open(r"E:\dam-platform\.data\qa_api_report.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print(rep.getvalue())
