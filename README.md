# AI-Powered Digital & Media Asset Management Platform

**One search box for everything** — type a natural-language query and get back matching
documents, images, audio, and video, jumping straight to the timestamp where the match occurs.

This repo implements the platform defined in the BRD (`BRD_AI_DAM_Platform`) and the
Technical Solution Architecture (`Technical_Solution_Architecture`): a VLM-native,
vector-first, frame-accurate, open-source stack. See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
for the reconciled specification and [`docs/ROADMAP.md`](docs/ROADMAP.md) for the build phases.

---

## What's built (status)

| Phase | Scope | Status |
|-------|-------|--------|
| **P0** | Monorepo, infra (Postgres+pgvector, Qdrant, OpenSearch, Redis, MinIO), schema, frame-accurate timecode, auth/RBAC | ✅ done & verified |
| **P1** | Document + image vertical slice: parse → embed → index → one-box hybrid search → web UI | ✅ done & verified |
| **P2** | Audio: Whisper ASR (GPU) + frame-mapped transcript + Smart Timeline Search + click-to-seek | ✅ done & verified |
| **P3** | Image/video AI: InsightFace faces + clustering + **face-search**, YOLO objects + object-search, video shots + frame-mapped markers | ✅ done & verified |
| **P4** | Hybrid RRF fusion + parallelized candidates + cross-encoder rerank | ✅ done & verified |
| **P5** | Governance & distribution: sharing (expiry/watermark), workflow approvals, **consent gating**, audit | ✅ done & verified |
| **P6** | Single-process serving, backups + DR (verified), idempotent jobs; scale-out path documented | ✅ done & verified |

> **Single-GPU serving:** all models live in one process (`worker.server`); the arq worker calls it over
> HTTP and holds no models. Heavy models (Whisper) release after use. On this 20 GB-VRAM / 32 GB-RAM host,
> raise the Windows pagefile to ~64 GB before running larger models concurrently (see `docs/SCALE_AND_DR.md`).

## Architecture (five planes, per TSA §2)

```
 Ingestion ──> AI Processing ──> Index & Storage ──> Retrieval ──> Application
 (upload,      (modality          (Qdrant vectors,    (hybrid      (FastAPI API +
  FFmpeg        pipelines:         OpenSearch BM25,    candidates    Next.js one-box
  frame map)    doc/img/aud/vid)   Postgres SoR)       + RRF fuse)   search UI)
```

- **apps/api** — FastAPI application plane (search, assets, collections, auth, admin). Holds **no** ML models.
- **services/ai-worker** — AI plane: an arq queue consumer (ingestion pipelines) **and** an HTTP inference server (query-time embeddings). Swappable for vLLM/TEI in P6.
- **apps/web** — Next.js "one search box" UI, asset explorer, viewers.
- **infra/** — Docker Compose stateful stores + Postgres schema.

## Prerequisites

- **Docker Desktop** (running), **Python 3.11+**, **Node 18+**, **FFmpeg** on PATH.
- For real AI: an NVIDIA GPU (this stack is tuned for ~20 GB VRAM — e.g. RTX 4000 Ada).
  CPU works for the API/search but embeddings will be slow.

## Quick start

```powershell
# 0. Configure
copy .env.example .env

# 1. Infrastructure
docker compose up -d
#    Postgres :5432 · Qdrant :6333 · OpenSearch :9200 · Redis :6379 · MinIO :9000/:9001

# 2. API (application plane) — no ML deps, starts instantly
cd apps\api
python -m venv .venv ; .venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m uvicorn app.main:app --port 8000
#    Bootstraps admin (admin@dam.local / admin12345), indices, collections, bucket.
#    Swagger at http://localhost:8000/docs

# 3. AI worker (AI plane) — heavy ML deps
cd services\ai-worker
python -m venv .venv ; .venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install torch --index-url https://download.pytorch.org/whl/cu124
.venv\Scripts\python -m pip install -e ".[ml]"
#    Inference server (query embeddings):
.venv\Scripts\python -m uvicorn worker.server:app --port 8100
#    Ingestion consumer (separate shell):
.venv\Scripts\arq worker.main.WorkerSettings

# 4. Web
cd apps\web
npm install
npm run dev          # http://localhost:3000
```

> **Windows note:** if `uv` fails to install `pywin32` with an "Access is denied" cache
> error, that's Windows Defender locking the binary mid-extraction — use `pip` instead
> of `uv` (as above), which extracts straight to site-packages.

## How a search works (the one box)

1. The UI POSTs the query to `/api/search`.
2. The API fans out **in parallel**: OpenSearch BM25 (keyword/metadata), Qdrant dense-text
   (semantic, via the worker's BGE-M3 embedding), Qdrant CLIP text→image, and OpenSearch
   timed-transcript search.
3. Candidates are fused with **Reciprocal Rank Fusion** (`apps/api/app/search/hybrid.py`).
4. The top page is hydrated from Postgres; media hits carry **frame-mapped timeline** points.
5. If the worker is down, search degrades cleanly to keyword-only.

## Frame accuracy (TSA §5)

Every temporal detection is stored as `frame_index` + exact rational `PTS` + drop-frame-aware
`SMPTE` + source `fps/timebase` — never bare milliseconds. See
`services/ai-worker/worker/timecode.py`. Playback seeks by frame index, so a hit reported at
frame N lands on frame N on screen.

## Repo layout

```
apps/api/            FastAPI application plane
apps/web/            Next.js front end
services/ai-worker/  AI processing + model-serving plane
infra/postgres/      schema (init.sql)
docker-compose.yml   infrastructure
docs/                reconciled architecture + roadmap
```
