"""Reliability watchdog — keeps the whole DAM stack alive and the GPU from saturating.
Automates the exact recoveries done by hand this session:

  • arq worker dies      → uploads silently stop becoming searchable. Restart it.
  • model server dies    → search errors / degrades. Restart it.
  • API (:8000) dies      → the whole product is down (UI + every query). Restart it.
  • infra container drops → OpenSearch/Qdrant/PG/Redis gone → API errors. `compose up -d`
                            to resume them (only when the Docker daemon is already running —
                            we never force-start Docker Desktop, so a deliberate Docker
                            shutdown to free resources for other GPU work is respected).
  • GPU VRAM saturates    → after heavy ingest the on-demand models + VLM fill VRAM, so every
                            search pages memory (~2s each). Free the VLM (ollama stop); if it's
                            still saturated AND the server is unresponsive, bounce it.

Run in the background:  services/ai-worker/.venv/Scripts/python.exe scripts/watchdog.py
Logs to .data/watchdog.log. This is the dev-box pragmatic supervisor; in production the same
recovery logic lives in systemd/k8s liveness probes.
"""
import datetime
import os
import socket
import subprocess
import time

import httpx

ROOT = r"E:\dam-platform"
AIWORKER = os.path.join(ROOT, "services", "ai-worker")
APIDIR = os.path.join(ROOT, "apps", "api")
PY = os.path.join(AIWORKER, ".venv", "Scripts", "python.exe")           # model server + worker venv
API_PY = os.path.join(APIDIR, ".venv", "Scripts", "python.exe")          # API venv (separate deps)
LOG = os.path.join(ROOT, ".data", "watchdog.log")
MODEL_URL = "http://127.0.0.1:8100"
API_URL = "http://127.0.0.1:8000"
API_HEALTH = f"{API_URL}/api/health"   # the API router mounts under /api
OLLAMA_MODEL = "qwen3-vl:8b"
VRAM_RELIEVE_MB = 17500     # sustained use above this → free the VLM
VRAM_BOUNCE_MB = 18800      # still above this AND unresponsive → bounce the model server
CHECK_SEC = 30
BOUNCE_COOLDOWN = 180
INFRA_COOLDOWN = 120        # don't hammer `compose up -d` more than once every 2 min
DETACH = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP (child survives us)

WORKER_PAT = "arq.*worker.main"
SERVER_PAT = "worker.server:app"
API_PAT = "app.main:app"
# Infra containers that must be listening for the API to function (host port → name).
INFRA_PORTS = {9200: "opensearch", 6333: "qdrant", 5432: "postgres", 6379: "redis", 9000: "minio"}


def log(msg):
    line = f"{datetime.datetime.now():%H:%M:%S}  {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def gpu_used_mb():
    try:
        out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                             capture_output=True, text=True, timeout=10)
        return int(out.stdout.strip().splitlines()[0])
    except Exception:
        return -1


def proc_count(pattern):
    ps = ("(@(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
          f"Where-Object {{ $_.CommandLine -match '{pattern}' }})).Count")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                             capture_output=True, text=True, timeout=15)
        return int((out.stdout.strip() or "0").splitlines()[-1])
    except Exception:
        return -1


def server_responsive():
    try:
        return httpx.post(f"{MODEL_URL}/embed/text", json={"text": "ping"}, timeout=6).status_code == 200
    except Exception:
        return False


def http_ok(url, timeout=5):
    try:
        return httpx.get(url, timeout=timeout).status_code == 200
    except Exception:
        return False


def port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except Exception:
        return False


def docker_daemon_up():
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=15).returncode == 0
    except Exception:
        return False


def launch(exe, args, cwd, name):
    out = open(os.path.join(ROOT, ".data", f"wd_{name}.out"), "a", encoding="utf-8")
    subprocess.Popen([exe] + args, cwd=cwd, stdout=out, stderr=subprocess.STDOUT,
                     creationflags=DETACH, close_fds=True)
    log(f"LAUNCHED {name}")


def ingest_active():
    try:
        from redis import Redis
        r = Redis(host="localhost", port=6379, db=0)
        return ((r.zcard("arq:queue") or 0) + len(r.keys("arq:in-progress:*"))) > 0
    except Exception:
        return False


def kill_server():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
                    f"Where-Object {{ $_.CommandLine -match '{SERVER_PAT}' }} | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                   capture_output=True, timeout=20)


def ensure_infra(last_infra):
    """If any infra container's port is closed AND the Docker daemon is up, `compose up -d` to
    resume them. We never start Docker Desktop itself — a deliberate Docker shutdown (e.g. to free
    RAM/GPU for other work on this shared box) is respected. Returns updated last_infra timestamp."""
    down = [name for port, name in INFRA_PORTS.items() if not port_open(port)]
    if not down:
        return last_infra
    now = time.time()
    if now - last_infra < INFRA_COOLDOWN:
        return last_infra
    if not docker_daemon_up():
        log(f"infra down {down} but Docker daemon is not running — leaving it (not force-starting Docker Desktop)")
        return now
    log(f"infra container(s) down {down} → docker compose up -d")
    try:
        subprocess.run(["docker", "compose", "up", "-d"], cwd=ROOT, capture_output=True, timeout=120)
    except Exception as e:
        log(f"compose up failed: {e}")
    return now


def main():
    log("watchdog started")
    last_bounce = 0.0
    last_infra = 0.0
    high = 0
    while True:
        try:
            # 0) infra first — the worker/server/API all depend on the containers being up.
            last_infra = ensure_infra(last_infra)
            if proc_count(WORKER_PAT) == 0:
                launch(PY, ["-m", "arq", "worker.main.WorkerSettings"], AIWORKER, "arqworker")
            if proc_count(SERVER_PAT) == 0:
                launch(PY, ["-m", "uvicorn", "worker.server:app", "--host", "127.0.0.1", "--port", "8100"],
                       AIWORKER, "modelserver")
                time.sleep(15)
            # API: process gone AND not answering health → relaunch. (Check both: a hung process
            # still counts as "present" but won't pass health; only relaunch when truly absent to
            # avoid spawning a second binder on a transient health blip.)
            if proc_count(API_PAT) == 0 and not http_ok(API_HEALTH):
                launch(API_PY, ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"],
                       APIDIR, "api")

            vram = gpu_used_mb()
            resp = server_responsive()
            high = high + 1 if vram >= VRAM_RELIEVE_MB else 0

            if high >= 2:                       # sustained pressure → relieve
                subprocess.run(["ollama", "stop", OLLAMA_MODEL], capture_output=True, timeout=30)
                log(f"VRAM {vram}MB ≥ {VRAM_RELIEVE_MB} → ollama stop {OLLAMA_MODEL}")
                time.sleep(4)
                v2, now = gpu_used_mb(), time.time()
                if v2 >= VRAM_BOUNCE_MB and not resp and not ingest_active() and now - last_bounce > BOUNCE_COOLDOWN:
                    kill_server(); time.sleep(2)
                    launch(PY, ["-m", "uvicorn", "worker.server:app", "--host", "127.0.0.1", "--port", "8100"],
                           AIWORKER, "modelserver")
                    last_bounce = now
                    log(f"VRAM still {v2}MB & server stuck → BOUNCED model server")
                high = 0
                log(f"relieved → {gpu_used_mb()}MB")

            log(f"ok vram={vram}MB server_up={resp} api_up={http_ok(API_HEALTH)} "
                f"worker={proc_count(WORKER_PAT)} ingest={ingest_active()}")
        except Exception as e:
            log(f"loop error: {e}")
        time.sleep(CHECK_SEC)


if __name__ == "__main__":
    main()
