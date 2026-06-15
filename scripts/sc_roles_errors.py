r"""Scenario harness — role-based access control + error handling across routers."""
import io

import httpx

API = "http://127.0.0.1:8000"
s = httpx.Client(timeout=60)


def login(u, p):
    r = s.post(f"{API}/api/auth/login", data={"username": u, "password": p},
               headers={"Content-Type": "application/x-www-form-urlencoded"})
    return r.json().get("access_token") if r.status_code == 200 else None


admin = {"Authorization": f"Bearer {login('admin@dam.local', 'admin12345')}"}
rep = io.StringIO()
p = f = 0


def check(name, cond, detail=""):
    global p, f
    ok = bool(cond); p += ok; f += (not ok)
    rep.write(f"[{'PASS' if ok else 'FAIL'}] {name}  {detail}\n")


# create (or reuse) a viewer user
s.post(f"{API}/api/admin/users", headers=admin, json={
    "email": "viewer@dam.local", "display_name": "Viewer", "password": "viewer12345", "role": "viewer"})
vtok = login("viewer@dam.local", "viewer12345")
check("viewer can log in", bool(vtok), "")
V = {"Authorization": f"Bearer {vtok}"}

# a real asset + person id to act on
aid = s.get(f"{API}/api/assets?type=image&limit=1", headers=admin).json()[0]["id"]
pid = (s.get(f"{API}/api/persons", headers=admin).json() or [{}])[0].get("id")

# ── viewer CAN read ──
check("viewer can search", s.post(f"{API}/api/search", headers=V, json={"q": "blue shirt", "limit": 3}).status_code == 200)
check("viewer can list assets", s.get(f"{API}/api/assets?limit=3", headers=V).status_code == 200)
check("viewer can view an asset", s.get(f"{API}/api/assets/{aid}", headers=V).status_code == 200)

# ── viewer CANNOT mutate (expect 401/403) ──
def forbidden(code): return code in (401, 403)
check("viewer CANNOT reprocess", forbidden(s.post(f"{API}/api/assets/{aid}/reprocess", headers=V).status_code),
      f"-> {s.post(f'{API}/api/assets/{aid}/reprocess', headers=V).status_code}")
check("viewer CANNOT delete asset", forbidden(s.delete(f"{API}/api/assets/{aid}", headers=V).status_code))
check("viewer CANNOT create collection", forbidden(s.post(f"{API}/api/collections", headers=V, json={"name": "x"}).status_code))
check("viewer CANNOT create share", forbidden(s.post(f"{API}/api/shares", headers=V, json={"scope_type": "asset", "scope_id": aid, "permission": "view"}).status_code))
check("viewer CANNOT create user", forbidden(s.post(f"{API}/api/admin/users", headers=V, json={"email": "x@x.com", "display_name": "x", "password": "xxxxxxxx", "role": "viewer"}).status_code))
if pid:
    check("viewer CANNOT change consent", forbidden(s.patch(f"{API}/api/persons/{pid}", headers=V, json={"consent_status": "denied"}).status_code))

# ── error handling: malformed ids -> 4xx not 5xx ──
for name, r in [
    ("GET collection bad-id", s.get(f"{API}/api/collections/not-a-uuid", headers=admin)),
    ("PATCH person bad-id", s.patch(f"{API}/api/persons/not-a-uuid", headers=admin, json={"display_name": "x"})),
    ("DELETE share bad-id", s.delete(f"{API}/api/shares/not-a-uuid", headers=admin)),
    ("GET workflow bad-id", s.get(f"{API}/api/assets/not-a-uuid/workflow", headers=admin)),
    ("GET asset media bad-id", s.get(f"{API}/api/assets/not-a-uuid/media", headers=admin)),
]:
    check(f"{name} -> 4xx not 5xx", 400 <= r.status_code < 500, f"-> {r.status_code}")

rep.write(f"\nSUMMARY: PASS={p} FAIL={f}\n")
with open(r"E:\dam-platform\.data\sc_roles_report.txt", "w", encoding="utf-8") as fh:
    fh.write(rep.getvalue())
print(rep.getvalue())
