"""First-boot initialisation: ensure storage bucket, search indices/collections,
and a default administrator exist. Safe to run on every startup (idempotent)."""
import logging
import uuid

from sqlalchemy import select

from .config import settings
from .db import SessionLocal
from .models import AppUser
from .search import opensearch_store, qdrant_store
from .security import hash_password
from .storage import ensure_bucket

log = logging.getLogger("dam.bootstrap")


async def run() -> None:
    # Object storage
    try:
        ensure_bucket()
        log.info("object storage bucket ready")
    except Exception as e:
        log.warning("bucket init skipped: %s", e)

    # Vector + keyword indices
    try:
        qdrant_store.ensure_collections()
        log.info("qdrant collections ready")
    except Exception as e:
        log.warning("qdrant init skipped: %s", e)
    try:
        opensearch_store.ensure_indices()
        log.info("opensearch indices ready")
    except Exception as e:
        log.warning("opensearch init skipped: %s", e)

    # Bootstrap admin
    try:
        async with SessionLocal() as db:
            existing = (await db.execute(
                select(AppUser).where(AppUser.email == settings.bootstrap_admin_email))).scalar_one_or_none()
            if not existing:
                db.add(AppUser(
                    id=str(uuid.uuid4()), email=settings.bootstrap_admin_email,
                    display_name="Administrator", hashed_pw=hash_password(settings.bootstrap_admin_password),
                    role="administrator", is_active=True,
                ))
                await db.commit()
                log.info("bootstrap admin created: %s", settings.bootstrap_admin_email)
    except Exception as e:
        log.warning("admin bootstrap skipped: %s", e)
