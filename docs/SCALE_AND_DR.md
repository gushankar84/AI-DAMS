# Scale-out & Disaster Recovery (P6)

This build runs the whole platform on one workstation. The architecture is designed so
the scale-out path is **configuration, not rewrites** — every heavy component sits behind
an interface that swaps for a distributed one.

## Source of truth vs. derived data

| Store | Role | On loss |
|-------|------|---------|
| **PostgreSQL** | System of record: assets, metadata, rights, persons/consent, shares, workflow, audit | Restore from dump — **must back up** |
| **MinIO / S3** | Original binaries + derived proxies/thumbnails/keyframes | Restore from backup (or re-upload originals) — **must back up** |
| Qdrant | Vector indexes (text/image/face) | **Derived** — rebuild by re-ingesting |
| OpenSearch | BM25 keyword + timed transcripts | **Derived** — rebuild by re-ingesting |

**DR principle:** back up Postgres + MinIO. Qdrant/OpenSearch can be rebuilt because
ingestion is **idempotent** (`stores.clear_derived` + `POST /api/assets/{id}/reprocess`).

## Backup / restore

```powershell
.\scripts\backup.ps1          # postgres.dump + qdrant snapshots + minio mirror
```
Restore:
```powershell
# Postgres
docker cp <backup>\postgres.dump dam-platform-postgres-1:/tmp/dam.dump
docker exec dam-platform-postgres-1 pg_restore -U dam -d dam --clean --if-exists /tmp/dam.dump
# MinIO (mirror back)
docker run --rm --network dam-platform_default -v "<backup>:/backup" minio/mc ... mc mirror /backup/minio s3/dam-assets
# Rebuild derived indexes (if Qdrant/OpenSearch were lost): reprocess all assets
#   for each asset id: POST /api/assets/{id}/reprocess   (idempotent)
```

## Scale-out path (configuration hooks already in place)

| Concern | MVP (this box) | Scale-out | Hook |
|---------|----------------|-----------|------|
| Model serving | in-process in `worker.server` | **vLLM + Text-Embeddings-Inference** on a GPU pool (Linux) | `serving_client` / `embed_client` HTTP routes are stable — point them at vLLM/TEI |
| Vectors | Qdrant | **Milvus** tiered RAM/NVMe/S3 for 100M+ | `search/constants.py` + `qdrant_store` → add `milvus_store` behind the same `search()` API |
| Multi-vector docs | dense text (BGE-M3) | **ColQwen3** late-interaction MaxSim in Qdrant | `dam_docpage` collection already reserved |
| Ingestion workers | single arq worker (`max_jobs=1`) | **KEDA**-autoscaled GPU worker pool on queue depth | arq/Redis queue already decouples ingest from serving |
| Models | 4B–8B tier, CPU/GPU mix | larger checkpoints (Qwen3-VL 32B, Whisper batch via Parakeet) | all model ids are `.env`-configurable |
| Throughput | one GPU | split **batch-embedding** vs **interactive** GPU pools | serving tier already separate from API/worker |

## Resilience already in place
- **Idempotent, resumable ingestion** (NFR-A3): re-running clears derived data first; arq retries failed jobs.
- **Heavy models released after use** (Whisper, VLM) to bound VRAM on a single GPU.
- **Graceful degradation**: if the model server is down, search falls back to keyword/BM25.
- **Backups + documented restore** for the source-of-truth stores.

## Host prerequisite for heavier concurrency
On a single 20 GB-VRAM / 32 GB-RAM box, raise the Windows pagefile to ~64 GB (commit limit).
Windows WDDM backs GPU allocations with system commit, so a small pagefile surfaces as
"CUDA out of memory" even when VRAM is free. This is the one host change recommended before
running larger models concurrently.
