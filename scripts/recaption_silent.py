import time, httpx
API="http://127.0.0.1:8000"
c=httpx.Client(base_url=API, timeout=600)
tok=c.post("/api/auth/login",data={"username":"admin@dam.local","password":"admin12345"},headers={"Content-Type":"application/x-www-form-urlencoded"}).json()["access_token"]
H={"Authorization":f"Bearer {tok}"}
ids=[x.strip() for x in open(r"E:\dam-platform\.data\recaption_ids.txt") if x.strip()]
for i in ids: c.post(f"/api/assets/{i}/reprocess",headers=H)
print(f"reprocessing {len(ids)} silent clips with captions...")
for _ in range(300):
    sts=[c.get(f"/api/assets/{i}",headers=H).json()["status"] for i in ids]
    if all(s in ("searchable","failed") for s in sts): break
    time.sleep(5)
caps=0
for i in ids:
    d=c.get(f"/api/assets/{i}",headers=H).json()
    caps+=sum(1 for m in d.get("markers",[]) if m.get("kind")=="scene")
print(f"done. scene captions now across the 4 clips: {caps}")
