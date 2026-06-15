"""arq worker — consumes the ingestion queue and runs the modality pipeline.

Run:  arq worker.main.WorkerSettings
Each asset is an idempotent, retryable job (TSA §4.1): re-running re-indexes
without creating duplicate assets (vectors are upserted; OpenSearch uses asset_id
as the doc id).
"""
from __future__ import annotations

import logging

from arq.connections import RedisSettings

from .config import settings
from . import stores
from .pipelines import audio, documents, images, video

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("dam.worker")

_PIPELINES = {
    "document": documents.process,
    "image": images.process,
    "audio": audio.process,
    "video": video.process,
}


async def ingest_asset(ctx, asset_id: str) -> str:
    """Route an uploaded asset to its modality pipeline."""
    rec = await stores.get_asset(asset_id)
    if not rec:
        log.error("asset %s not found", asset_id)
        return "not_found"
    asset = dict(rec)
    pipeline = _PIPELINES.get(asset["type"])
    if not pipeline:
        await stores.set_status(asset_id, "failed", f"no pipeline for type {asset['type']}")
        return "no_pipeline"
    try:
        await stores.clear_derived(asset_id)   # idempotent re-ingestion (NFR-A3)
        await pipeline(asset)
        return "ok"
    except Exception as e:
        log.exception("ingest failed for %s", asset_id)
        await stores.set_status(asset_id, "failed", str(e)[:500])
        raise


class WorkerSettings:
    functions = [ingest_asset]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = 1                 # single GPU + single model server; serialize to avoid
                                 # face-clustering races and VRAM contention
    max_tries = 2                # one retry covers a transient blip (e.g. model server cold-
                                 # loading); a genuinely-bad asset stops here instead of
                                 # re-running the whole GPU pipeline 5× (arq's default).
    job_timeout = 3600           # large media can take a while
    keep_result = 3600
