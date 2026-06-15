# Implementation Roadmap

Phased per TSA §13, adapted to a single-GPU local build. Each phase leaves the system
runnable. Tracked live in the session task list.

## P0 — Foundations ✅ (done & verified)
- Monorepo: `apps/api`, `apps/web`, `services/ai-worker`, `infra/`.
- Docker Compose: Postgres+pgvector, Qdrant, OpenSearch, Redis, MinIO. **Verified up.**
- Postgres schema (TSA §8), frame-accurate `marker`/`stream`/`transcript`. **Verified.**
- Frame-accurate timecode utility (probe, seconds→frame snap, SMPTE drop-frame).
- Auth/RBAC (JWT, roles), audit logging, bootstrap admin/indices/collections/bucket. **Verified login + search.**

## P1 — Document & text/image search ✅ (core slice done & verified)
- [x] Document pipeline: Docling parse → chunk → BGE-M3 embed → Qdrant `dam_text` + OpenSearch BM25.
- [x] Image pipeline: CLIP embed → Qdrant `dam_image`, deterministic thumbnail.
- [x] One-box hybrid search (BM25 + dense-text + CLIP text→image + transcript) with RRF fusion.
- [x] Next.js UI: login, one-box search, results grid, asset viewer.
- [x] **End-to-end verified on GPU with real data** — BRD `.docx` retrieved by natural-language
      semantic query (keyword+semantic fusion); 4 images retrieved by correct cross-modal text→image ranking.
      Repro: `scripts/e2e.py`, `scripts/e2e_images.py`.
- [ ] *(P1-advanced)* ColQwen3 multi-vector page retrieval (`dam_docpage`) for visual document search.
- [ ] *(carry to P4)* Latency tuning to 1–2 s NFR (pool/batch the two query embeddings); `date`/`type` sort over full candidate set.

## P2 — Audio search ✅ (verified on GPU)
- [x] faster-whisper `large-v3` ASR (cuda/float16) with word timestamps. Verified: Hindi clip transcribed.
- [x] Frame-mapped transcript (sample-accurate frame + rational PTS + clock); timed segments in
      `dam-transcripts` + dense transcript vectors in `dam_text`.
- [x] Smart Timeline Search verified: query "car accident" → asset @ 00:00:00.180 with snippet.
- [x] Idempotent re-ingestion (`clear_derived`) + `POST /assets/{id}/reprocess` (FR-ADM-3, NFR-A3).
- [x] Audio viewer: synced transcript with click-to-seek (frame/seconds). Whisper VRAM released before embedding.
- [ ] *(optional)* pyannote diarization (needs HF token); waveform visualization in the viewer.
- ⚠️ Concurrent semantic-while-ingesting limited by host memory — see "Resource constraint" below.

## Architecture note — single model-serving process (pulled forward from P6)
On a single 20 GB GPU / 32 GB RAM / fixed-20 GB-pagefile host, loading models in
*both* the inference server and the arq worker exhausted the system commit limit.
Resolved by consolidating **all GPU models into one serving process** (`worker.server`,
the TSA §9 model-serving plane): the arq worker now holds **no** ML libraries and
calls the server over HTTP (`worker/serving_client.py`); heavy models (Whisper; the
P3 VLM) are released after use. Verified: doc + image + audio all re-ingest and
semantic search fires across modalities (incl. cross-lingual EN→HI audio match).
Model names/device are env-configurable for when more memory is available.

## P3 — Image & video AI ✅ (verified on GPU)
- [x] InsightFace (RetinaFace+ArcFace) detection+embedding; `worker/vision.py`.
- [x] Name-once person clustering (`face_nearest` + `create_person`, cosine 0.35, serialized) → `dam_face`.
      Verified: Raghupathy2/3 cluster; same-person cosine 0.57–0.58, cross-person ~0.05.
- [x] YOLO closed-set object detection (`yolov8n`) → object markers + OpenSearch `labels`.
      Verified: object search "sports ball" / "person" finds the right video.
- [x] **Face-search API** `POST /api/search/face` (upload → ranked matches, audited NFR-S4). Verified.
- [x] Video pipeline: probe → PySceneDetect shots → per-shot keyframe → enrich (faces/objects/scene)
      frame-mapped to the video grid + ASR aligned to frames. Verified: clip → 1 shot, 2 faces, 3 objects, SMPTE markers.
- [x] Video/asset viewer: detection + transcript panels with click-to-seek (`start_seconds`).
- [x] Optional scene/activity caption via Ollama VLM (`worker/caption.py`, configurable; off by default).
- [ ] *(advanced, needs more VRAM/pagefile)* open-vocab detection (Grounding DINO / YOLO-World); Qwen3-VL video grounding; proxy/HLS generation; diarization.

## P4 — Hybrid fusion + reranking ✅ (verified)
- [x] RRF fusion across BM25 + dense-text + CLIP image + transcript (from P1, `search/hybrid.py`).
- [x] **Parallelized candidate generation** (ThreadPoolExecutor): 2 query embeddings + index queries
      run concurrently → warm search ~2.7 s (was ~5 s).
- [x] **Cross-encoder rerank** (`bge-reranker-base`, server `/rerank`): 336 ms warm, scores calibrated 0–1.
      Blended 0.6·rerank + 0.4·fused so thin-text image/video results aren't demoted. Verified: keeps the
      right doc #1 for a privacy/faces query; toggle via `rerank` flag.
- [ ] *(future)* explicit intent/modality query planner; cache query embeddings; push latency under 2 s with rerank.

## P5 — Governance & distribution ✅ (verified)
- [x] Sharing (`/api/shares`, `/api/public/shares/{token}`): asset/collection, view/download/edit/admin,
      expiring links, watermark flag, presigned URLs. Public resolver honors expiry/permission. Verified.
- [x] Workflow engine (`/api/assets/{id}/workflow`): role-gated transitions + auditable history. Verified.
- [x] **Consent gating** (NFR-S4): denied/revoked persons excluded from facial search; reviewer-gated
      consent changes; named clusters. Verified: deny → excluded, grant → restored.
- [x] Full audit log of search/view/upload/share/workflow/person/share-access (`audit_log`).
- [x] Identity fidelity (§7): deterministic Lanczos thumbnails; no generative model on any face path.
- [x] RBAC across endpoints (`require_role`).

## P6 — Scale, serving, resilience ✅ (local resilience verified; scale-out documented)
- [x] **Single model-serving process** (pulled forward) — the abstraction that swaps to vLLM/TEI.
- [x] **Backups + DR**: `scripts/backup.ps1` (Postgres dump + Qdrant snapshots + MinIO mirror) — verified
      (postgres.dump + 3 snapshots + 26 binaries). Restore + rebuild procedure in `docs/SCALE_AND_DR.md`.
- [x] **Idempotent, resumable ingestion** (`clear_derived` + `/reprocess`); arq retries; heavy models released.
- [x] **Graceful degradation** (model server down → keyword search).
- [x] **Scale-out path documented** with in-place config hooks: vLLM+TEI serving, Milvus tier,
      ColQwen multi-vector, KEDA worker autoscaling, larger model checkpoints (all `.env`-driven). See `docs/SCALE_AND_DR.md`.
- [ ] *(actual multi-node deployment)* stand up vLLM/TEI + Milvus + KEDA on a GPU cluster — beyond a single workstation; hooks ready.

---

## UI — full application (all screens) ✅ (built & UAT-verified)
Complete Next.js app, not just search. Built via a fan-out workflow against a fixed
contract (api client + design system + AppShell), then UAT-driven through the live app.
- [x] Screens: **Login, Dashboard, Universal+Face Search, Asset Explorer (grid/list/trash/multi-select),
      Upload Center (drag-drop + lifecycle), Collections, Asset Viewer (player + transcript/detections/metadata/workflow tabs,
      click-to-seek), Distribution, Workflows, Admin (Users/People&Consent/Processing/Models), Reports.**
- [x] Backend endpoints added for the UI: `/api/stats` (+ activity/trending/most-viewed), admin users/queue/models,
      soft-delete + restore + Trash, workflow/status/collection list filters.
- [x] **Production build passes** (12 routes, 0 errors). **UAT**: every screen exercised live (screenshots),
      read+write flows verified (search, face search, viewer markers, metadata save, collection create,
      workflow transition, trash→restore, sharing, consent gating, admin tabs).
- [x] **Bug found & fixed in UAT**: Admin → Models read wrong response keys (`text_embed`/`server`) vs API
      (`configured`/`model_server`) → showed "not configured / UNREACHABLE"; corrected + re-verified.
- [x] Fixed `/search` static-prerender bailout (Suspense boundary around useSearchParams).

### Known follow-ups / tech debt
- `qdrant-client` 1.18 warns against server 1.12.4 (works fine); align versions when convenient.
- Cold-start search latency (~first request) is high while clients warm up; negligible after.
- Replace `allow_origins=["*"]` CORS with an allowlist before any non-local deployment.
- Add Alembic migrations (schema currently applied via `init.sql` on first boot).
