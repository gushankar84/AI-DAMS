import time, httpx
API="http://127.0.0.1:8000"
c=httpx.Client(base_url=API, timeout=20)
tok=c.post("/api/auth/login",data={"username":"admin@dam.local","password":"admin12345"},headers={"Content-Type":"application/x-www-form-urlencoded"}).json()["access_token"]
H={"Authorization":f"Bearer {tok}"}
fid=[a["id"] for a in c.get("/api/assets?limit=200",headers=H).json() if a["id"].startswith("ff6bfb8d")][0]
for _ in range(180):
    try: st=c.get(f"/api/assets/{fid}",headers=H).json()["status"]
    except Exception: time.sleep(5); continue
    if st in ("searchable","failed"): break
    time.sleep(5)
print(f"ARUNI.pdf final status: {st}")
