"""Enqueue ingestion jobs onto Redis (arq). The ai-worker consumes them.

Kept dependency-light so the API never imports ML libraries.
"""
from arq import create_pool
from arq.connections import RedisSettings

from .config import settings

_pool = None


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(settings.redis_url)


async def get_pool():
    global _pool
    if _pool is None:
        _pool = await create_pool(_redis_settings())
    return _pool


async def enqueue_ingest(asset_id: str) -> None:
    """Queue an asset for the modality pipeline (router decides doc/image/audio/video)."""
    pool = await get_pool()
    await pool.enqueue_job("ingest_asset", asset_id)
