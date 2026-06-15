# Reliability / Ops

Single 20GB GPU shared by the resident query embedders, the on-demand ingest models
(Whisper/YOLO/InsightFace), and the Ollama VLM (qwen3-vl). Two failure modes recur:

1. **Ingest worker stops** → uploads queue in Redis but never become searchable.
2. **GPU VRAM saturates** after heavy ingest → every search pages memory (~2s each); the
   model server can refuse new connections.

## Watchdog (automated recovery)
`scripts/watchdog.py` polls every 30s and auto-recovers:
- arq worker process gone → relaunch (`arq worker.main.WorkerSettings`).
- model server process gone → relaunch (`uvicorn worker.server:app :8100`).
- VRAM ≥ 17.5GB sustained → `ollama stop qwen3-vl:8b` (frees ~5.8GB; reloads on next caption).
- still ≥ 18.8GB AND server unresponsive AND not mid-ingest → bounce the model server.
Logs to `.data/watchdog.log`. Verified: killing the worker → relaunched within 30s.

Run now (session):   `services/ai-worker/.venv/Scripts/python.exe scripts/watchdog.py`
Make permanent:      `powershell -ExecutionPolicy Bypass -File scripts/install_watchdog_task.ps1`
                     (registers logon Scheduled Task "DAM-Watchdog"; survives reboots)

## Manual recovery (if ever needed)
- Search slow + `nvidia-smi` near 20GB:  `ollama stop qwen3-vl:8b`  then bounce the model server.
- Uploads not searchable:  check the arq worker is running; process a stuck asset directly via
  `stores.get_asset(id)` → `documents.process(asset)` (idempotent).

## Stronger isolation (future)
CPU-pin the QUERY embedders (BGE/SigLIP) so search never contends with ingest GPU (+~1s but
contention-proof) — eliminates the VRAM-pressure class entirely rather than recovering from it.
