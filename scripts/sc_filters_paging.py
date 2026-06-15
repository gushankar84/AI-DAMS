r"""Scenario harness — filters, sort, pagination, boundary inputs."""
import io

import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=60)
tok = s.post(f"{API}/api/auth/login", data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}
rep = io.StringIO()
p = f = 0


def check(name, cond, detail=""):
    global p, f
    ok = bool(cond); p += ok; f += (not ok)
    rep.write(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}\n")


def search(**kw):
    return s.post(f"{API}/api/search", headers=H, json=kw).json()


# 1) Type filters return only that type
for t in ("image", "video", "audio", "document"):
    r = search(q="a", types=[t], limit=20)
    types = {h["type"] for h in r["hits"]}
    check(f"type filter '{t}' pure", types <= {t}, f"got {types}")

# 2) sort=type groups by type
r = search(q="a person", sort="type", limit=20)
ts = [h["type"] for h in r["hits"]]
check("sort=type is grouped", ts == sorted(ts), f"{ts[:6]}")

# 3) sort=date actually orders by date (fetch created_at to verify)
r = search(q="a person", sort="date", limit=10)
ids = [h["asset_id"] for h in r["hits"]]
dates = []
for aid in ids[:6]:
    d = s.get(f"{API}/api/assets/{aid}", headers=H).json()
    dates.append(d.get("created_at"))
nonnull = [d for d in dates if d]
check("sort=date is descending", nonnull == sorted(nonnull, reverse=True), f"dates={[str(d)[:10] for d in dates]}")

# 4) date range filter
r_all = search(q="a", date_from="2000-01-01", limit=50)
r_none = search(q="a", date_to="2000-01-01", limit=50)
check("date_from(2000)=keeps results", r_all["total"] > 0, f"n={r_all['total']}")
check("date_to(2000)=excludes recent", r_none["total"] == 0, f"n={r_none['total']}")

# 5) pagination — no overlap, beyond-end empty
a = search(q="a person", limit=3, offset=0, rerank=False)
b = search(q="a person", limit=3, offset=3, rerank=False)
ida = {h["asset_id"] for h in a["hits"]}; idb = {h["asset_id"] for h in b["hits"]}
check("pagination pages disjoint", ida.isdisjoint(idb), f"overlap={len(ida & idb)}")
far = search(q="a person", limit=5, offset=10000)
check("offset beyond end -> empty, no error", far["hits"] == [], f"n_hits={len(far['hits'])}")

# 6) boundary inputs (must not 500)
for name, kw in [("limit=0", dict(q="blue shirt", limit=0)),
                 ("limit=100000", dict(q="blue shirt", limit=100000)),
                 ("offset=-5", dict(q="blue shirt", offset=-5)),
                 ("whitespace query", dict(q="     ", limit=5))]:
    try:
        r = s.post(f"{API}/api/search", headers=H, json=kw)
        check(f"boundary {name} handled (not 500)", r.status_code != 500, f"-> {r.status_code}")
    except Exception as e:
        check(f"boundary {name} handled", False, str(e)[:60])

rep.write(f"\nSUMMARY: PASS={p} FAIL={f}\n")
with open(r"E:\dam-platform\.data\sc_filters_report.txt", "w", encoding="utf-8") as fh:
    fh.write(rep.getvalue())
print(rep.getvalue())
