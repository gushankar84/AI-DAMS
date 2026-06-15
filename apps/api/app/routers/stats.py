"""Dashboard & reports data (BRD §5.1): asset counts, processing queue, recent
activity, trending searches, most-viewed."""
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..security import CurrentUser

router = APIRouter(prefix="/api/stats", tags=["stats"])


@router.get("")
async def overview(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    async def scalars(sql: str):
        return {r[0]: r[1] for r in (await db.execute(text(sql))).all()}

    by_type = await scalars(
        "SELECT type, count(*) FROM asset WHERE deleted_at IS NULL GROUP BY type")
    by_status = await scalars(
        "SELECT status, count(*) FROM asset WHERE deleted_at IS NULL GROUP BY status")
    by_workflow = await scalars(
        "SELECT workflow, count(*) FROM asset WHERE deleted_at IS NULL GROUP BY workflow")
    totals = (await db.execute(text(
        "SELECT count(*), COALESCE(sum(size_bytes),0) FROM asset WHERE deleted_at IS NULL"))).one()
    persons = (await db.execute(text("SELECT count(*) FROM person"))).scalar() or 0
    collections = (await db.execute(text("SELECT count(*) FROM collection"))).scalar() or 0
    trash = (await db.execute(text(
        "SELECT count(*) FROM asset WHERE deleted_at IS NOT NULL"))).scalar() or 0

    # Processing queue: anything not yet searchable/failed.
    queue = {k: by_status.get(k, 0) for k in
             ("uploaded", "processing", "extracting", "indexed", "searchable", "failed")}

    return {
        "total_assets": totals[0],
        "storage_bytes": int(totals[1]),
        "by_type": {k: by_type.get(k, 0) for k in ("document", "image", "audio", "video")},
        "by_status": by_status,
        "by_workflow": by_workflow,
        "queue": queue,
        "persons": persons,
        "collections": collections,
        "trash": trash,
    }


@router.get("/activity")
async def activity(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)], limit: int = 25):
    rows = (await db.execute(text(
        """SELECT a.action, a.target_type, a.target_id, a.detail, a.created_at, u.display_name
           FROM audit_log a LEFT JOIN app_user u ON u.id = a.actor_id
           ORDER BY a.created_at DESC LIMIT :lim"""), {"lim": limit})).all()
    return [{"action": r[0], "target_type": r[1], "target_id": str(r[2]) if r[2] else None,
             "detail": r[3], "created_at": r[4].isoformat(), "actor": r[5] or "system"} for r in rows]


@router.get("/trending")
async def trending(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)], limit: int = 10):
    rows = (await db.execute(text(
        """SELECT detail->>'q' AS q, count(*) AS n FROM audit_log
           WHERE action='search' AND COALESCE(detail->>'q','') <> ''
           GROUP BY q ORDER BY n DESC LIMIT :lim"""), {"lim": limit})).all()
    return [{"query": r[0], "count": r[1]} for r in rows]


@router.get("/most-viewed")
async def most_viewed(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)], limit: int = 8):
    rows = (await db.execute(text(
        """SELECT a.id, a.title, a.filename, a.type, a.thumbnail_uri, count(*) AS views
           FROM audit_log l JOIN asset a ON a.id = l.target_id
           WHERE l.action='view' AND a.deleted_at IS NULL
           GROUP BY a.id, a.title, a.filename, a.type, a.thumbnail_uri
           ORDER BY views DESC LIMIT :lim"""), {"lim": limit})).all()
    return [{"asset_id": str(r[0]), "title": r[1], "filename": r[2], "type": r[3],
             "thumbnail_uri": r[4], "views": r[5]} for r in rows]
