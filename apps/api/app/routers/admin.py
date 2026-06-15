"""Administration (BRD §5.15): users & roles, AI models / model-server health,
processing queue controls."""
import os
import uuid
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..config import settings
from ..db import get_db
from ..models import AppUser
from ..queue import enqueue_ingest
from ..security import hash_password, require_role

router = APIRouter(prefix="/api/admin", tags=["admin"])
WORKER_URL = os.environ.get("AI_WORKER_URL", "http://localhost:8100")


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    is_active: bool


class UserCreate(BaseModel):
    email: EmailStr
    display_name: str
    password: str
    role: Literal["viewer", "contributor", "reviewer", "distributor", "administrator"] = "viewer"


@router.get("/users", response_model=list[UserOut])
async def list_users(user: Annotated[AppUser, Depends(require_role("administrator"))],
                     db: Annotated[AsyncSession, Depends(get_db)]):
    rows = (await db.execute(select(AppUser).order_by(AppUser.created_at))).scalars().all()
    return [UserOut(id=u.id, email=u.email, display_name=u.display_name, role=u.role,
                    is_active=u.is_active) for u in rows]


@router.post("/users", response_model=UserOut)
async def create_user(body: UserCreate,
                      user: Annotated[AppUser, Depends(require_role("administrator"))],
                      db: Annotated[AsyncSession, Depends(get_db)]):
    exists = (await db.execute(select(AppUser).where(AppUser.email == body.email))).scalar_one_or_none()
    if exists:
        raise HTTPException(409, "Email already exists")
    u = AppUser(id=str(uuid.uuid4()), email=body.email, display_name=body.display_name,
                hashed_pw=hash_password(body.password), role=body.role, is_active=True)
    db.add(u)
    await db.commit()
    await audit.log(db, user.id, "user_create", "user", u.id, {"email": body.email, "role": body.role})
    return UserOut(id=u.id, email=u.email, display_name=u.display_name, role=u.role, is_active=True)


@router.get("/models")
async def models_status(user: Annotated[AppUser, Depends(require_role("administrator"))]):
    """Configured AI models + live model-server capabilities (NFR-M1 swappable models)."""
    configured = {
        "text_embed": settings.text_embed_model, "reranker": getattr(settings, "rerank_model", None),
        "image_embed": settings.image_embed_model, "asr": settings.asr_model,
    }
    server = {"reachable": False, "capabilities": {}}
    try:
        r = httpx.get(f"{WORKER_URL}/health", timeout=5)
        if r.status_code == 200:
            server = {"reachable": True, "capabilities": r.json().get("capabilities", {})}
    except Exception:
        pass
    return {"configured": configured, "model_server": server}


@router.get("/queue")
async def queue(user: Annotated[AppUser, Depends(require_role("administrator"))],
                db: Annotated[AsyncSession, Depends(get_db)]):
    rows = (await db.execute(text(
        "SELECT status, count(*) FROM asset WHERE deleted_at IS NULL GROUP BY status"))).all()
    by_status = {r[0]: r[1] for r in rows}
    recent = (await db.execute(text(
        """SELECT id, title, filename, type, status, error_detail FROM asset
           WHERE status IN ('uploaded','processing','extracting','failed') AND deleted_at IS NULL
           ORDER BY updated_at DESC LIMIT 25"""))).all()
    return {"by_status": by_status,
            "active": [{"asset_id": str(r[0]), "title": r[1], "filename": r[2], "type": r[3],
                        "status": r[4], "error": r[5]} for r in recent]}


@router.post("/reprocess-failed")
async def reprocess_failed(user: Annotated[AppUser, Depends(require_role("administrator"))],
                           db: Annotated[AsyncSession, Depends(get_db)]):
    ids = (await db.execute(text("SELECT id FROM asset WHERE status='failed' AND deleted_at IS NULL"))).scalars().all()
    for aid in ids:
        await db.execute(text("UPDATE asset SET status='uploaded', error_detail=NULL WHERE id=:i"),
                         {"i": aid})
    await db.commit()
    for aid in ids:
        await enqueue_ingest(str(aid))
    return {"requeued": len(ids)}
