# Reconciled Architecture & Specification

This document reconciles the **Business Requirements Document (BRD v1.0)** with the
**Technical Solution Architecture (TSA v1.0)** into one canonical specification for the
build. Where the two sources diverged, the resolution and rationale are recorded here.

---

## 1. Source reconciliation

The BRD and TSA were **not a matched pair**: the TSA describes itself as a companion to a
"PRD v2.0" and references requirements (frame-accurate timecode, 100% facial-identity
fidelity, "pgvector for everything") that do not appear in BRD v1.0, and its recommended
stack supersedes the BRD's Section 12–13 guidance. Reconciliation decisions:

| Topic | BRD v1.0 | TSA v1.0 | **Decision (this build)** |
|-------|----------|----------|---------------------------|
| Doc retrieval | OCR-first (PaddleOCR/Tesseract) | Vector-first: ColQwen3 multi-vector page embeddings, OCR as metadata | **TSA.** Vector-first; structured text still produced for display/citation. MVP ships dense text + BM25; ColQwen multi-vector is P1-advanced. |
| Image/video AI | YOLO + CLIP + Immich reference | Qwen3-VL family (VLM, embeddings, reranker) + InsightFace + YOLO/open-vocab | **TSA**, with MVP using OpenCLIP (light, 20 GB-VRAM-friendly) and swapping to Qwen3-VL-Embedding as VRAM/throughput allow. |
| Vector store | OpenSearch + Qdrant | Qdrant primary (MaxSim multi-vector), Milvus at largest tier, pgvector for relational | **TSA.** Qdrant primary; Milvus deferred to P6; pgvector kept for relational + MVP single-vector. |
| ASR | Whisper | Whisper v3 / Qwen3-ASR + Parakeet for batch | **TSA.** `faster-whisper large-v3` for the local build (runs well on Windows CUDA, low VRAM). |
| Timecode | Not specified | Frame index + PTS + SMPTE, drop-frame aware (hard contract) | **TSA.** Implemented as a hard contract (`worker/timecode.py`, `marker`/`stream`/`transcript` schema). |
| Identity fidelity | Not specified | No generative models on identity paths; deterministic thumbnails | **TSA.** Thumbnails via Lanczos only; face indexing uses original pixels. |
| Model serving | Not specified | vLLM + TEI | **TSA**, deferred: MVP serves models in-process (vLLM/TEI need Linux). Routes are stable so the swap is transparent. |
| Backend / Frontend | FastAPI / React/Next.js | FastAPI / Next.js+React | **Agree.** |

**Net:** the TSA is the authoritative technical design; the BRD supplies the functional
scope, MoSCoW priorities, personas, NFRs, and acceptance criteria, all of which are honored.

## 2. Functional coverage map (BRD §5 → implementation)

| BRD module | Where |
|------------|-------|
| §5.2 Universal Search | `apps/api/app/routers/search.py` + `search/hybrid.py` (RRF over BM25 + dense + image + transcript) |
| §5.3 Asset Explorer | `apps/web/app/page.tsx` (grid, type filters) |
| §5.4 Upload Center | `apps/api/app/routers/assets.py` `upload` (+ checksum dedup, lifecycle status) |
| §5.5 AI Processing Engine | `services/ai-worker/worker/pipelines/*` (document/image now; audio/video P2/P3) |
| §5.6 Smart Timeline Search | `transcript` index + `TimelineHit` in search response |
| §5.7 Facial Search | `dam_face` Qdrant collection + `person` table + consent gating (P3) |
| §5.10 Collections | `apps/api/app/routers/collections.py` (by reference, no duplication) |
| §5.11 Asset Viewer | `apps/web/app/asset/[id]/page.tsx` (doc/image/audio/video + markers/transcript panels) |
| §5.12 Metadata Mgmt | `asset` standard fields + `PATCH /api/assets/{id}` (AI-metadata correction) |
| §5.13 Distribution | `share` table + permissions/expiry/watermark (P5) |
| §5.14 Workflow | `workflow` state + `workflow_state` history (P5) |
| §5.15 / §9 Administration & RBAC | `app_user.role` + `require_role` dependency |
| §6 NFRs | async queue workers (NFR-P3), TLS/RBAC/audit (§11), retryable idempotent jobs (NFR-A3) |

## 3. Component & data flow

See [`README.md`](../README.md) for the five-plane diagram and the step-by-step search flow.

**System-of-record (Postgres):** `asset, stream, marker, transcript, person, collection,
collection_item, share, workflow_state, audit_log, app_user` — schema in
[`infra/postgres/init.sql`](../infra/postgres/init.sql).

**Indexes:**
- Qdrant: `dam_text` (BGE-M3, 1024-d), `dam_image` (CLIP, 512-d), `dam_face` (ArcFace, 512-d), `dam_docpage` (ColQwen multi-vector, P1-adv).
- OpenSearch: `dam-assets` (BM25 keyword/metadata), `dam-transcripts` (timed segments).
- Contract for names/dims: `apps/api/app/search/constants.py` ⇄ `services/ai-worker/worker/config.py`.

## 4. Hardware adaptation (single 20 GB GPU)

The TSA targets tens of millions of assets across multiple GPUs. This build is tuned to run
on one RTX 4000 Ada (20 GB) workstation while preserving the architecture:
- Qdrant only (Milvus deferred); OpenSearch heap capped at 512 MB.
- 4B–8B model tier; OpenCLIP for image embeddings in the MVP.
- In-process model serving (vLLM/TEI deferred to P6, which needs Linux/WSL2 GPU).
- Model weights pinned to `E:\` via `HF_HOME` to keep the system drive and the full `D:\` clear.

## 5. Non-negotiables carried from the TSA

1. **Frame accuracy** is enforced at ingestion, storage, and playback.
2. **Identity fidelity:** no generative/upscaling model on any face/identity path; thumbnails are deterministic.
3. **Embeddings are sensitive data** (face embeddings can re-identify) — access-controlled, encrypted at rest in production.
4. **Idempotent, resumable** AI jobs; ingestion is decoupled from query latency.
