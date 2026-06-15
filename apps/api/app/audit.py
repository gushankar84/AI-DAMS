"""Audit logging helper (NFR-S3): records access, search, downloads, shares, admin."""
from sqlalchemy.ext.asyncio import AsyncSession

from .models import AuditLog


async def log(db: AsyncSession, actor_id: str | None, action: str,
              target_type: str | None = None, target_id: str | None = None,
              detail: dict | None = None, ip: str | None = None) -> None:
    db.add(AuditLog(
        actor_id=actor_id, action=action, target_type=target_type,
        target_id=target_id, detail=detail or {}, ip=ip,
    ))
    await db.commit()
