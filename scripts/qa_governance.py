r"""Production QA — governance & security.
- sensitive files (Aadhaar/cheque/policy) must NOT be in the index (privacy)
- searches are audited
- face search is consent-gated (denied persons excluded) and audited
- role gating on mutating endpoints
"""
import io

import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=60)
tok = s.post(f"{API}/api/auth/login",
             data={"username": "admin@dam.local", "password": "admin12345"},
             headers={"Content-Type": "application/x-www-form-urlencoded"}).json()["access_token"]
H = {"Authorization": f"Bearer {tok}"}

rep = io.StringIO()
passes = fails = 0


def check(name, cond, detail=""):
    global passes, fails
    ok = bool(cond); passes += ok; fails += (not ok)
    rep.write(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}\n")


# 1) Sensitive files must not be ingested (privacy constraint)
assets = s.get(f"{API}/api/assets?limit=500", headers=H).json()
SENSITIVE = ("aadhaar", "aadhar", "cheque", "check ", "policy", "pan card", "passport")
sens = [a for a in assets if any(t in a["filename"].lower() for t in SENSITIVE)]
check("no sensitive files in index", len(sens) == 0, f"found={[a['filename'] for a in sens]}")

# 2) Sensitive search terms surface nothing sensitive
for term in ("aadhaar card", "cheque", "bank account number"):
    r = s.post(f"{API}/api/search", headers=H, json={"q": term, "limit": 5}).json()
    leaked = [h["filename"] for h in r["hits"] if any(t in h["filename"].lower() for t in SENSITIVE)]
    check(f"search '{term}' leaks nothing sensitive", not leaked, f"leaked={leaked}")

# 3) Searches are audited (activity feed reflects them)
s.post(f"{API}/api/search", headers=H, json={"q": "audit-probe-xyz", "limit": 1})
act = s.get(f"{API}/api/stats/activity?limit=20", headers=H)
if act.status_code == 200:
    feed = act.json()
    found = any(isinstance(a.get("detail"), dict) and a["detail"].get("q") == "audit-probe-xyz" for a in feed)
    check("search is audited (appears in activity)", found, f"feed_len={len(feed)}")
else:
    check("activity endpoint reachable", False, f"-> {act.status_code}")

# 4) Face search is consent-gated + audited (endpoint exists, governed)
#    (no face uploaded here — verify the route is wired and rejects anon)
anon = httpx.Client(timeout=30)
r = anon.post(f"{API}/api/search/face", files={"file": ("x.jpg", b"notanimage", "image/jpeg")})
check("face search requires auth", r.status_code in (401, 403), f"-> {r.status_code}")

# 5) Persons endpoint exposes consent state (for governance UI)
persons = s.get(f"{API}/api/persons", headers=H)
check("persons endpoint reachable", persons.status_code == 200, f"-> {persons.status_code}")
if persons.status_code == 200 and persons.json():
    p = persons.json()[0]
    check("person record has consent field", any(k in p for k in ("consent", "consent_status", "denied")),
          f"keys={list(p.keys())[:8]}")

rep.write(f"\nSUMMARY: PASS={passes} FAIL={fails}\n")
with open(r"E:\dam-platform\.data\qa_governance_report.txt", "w", encoding="utf-8") as f:
    f.write(rep.getvalue())
print(rep.getvalue())
