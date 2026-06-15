"""Distribution (BRD §5.13): share assets/collections with per-share permissions,
expiring public links, and watermarked previews. All shares and accesses audited.
"""
import asyncio
import io
import secrets
import uuid
from datetime import datetime, timezone
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from PIL import Image, ImageDraw, ImageFont
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db import get_db
from ..models import AppUser, Asset, CollectionItem, Share
from ..security import ROLE_RANK, CurrentUser, require_role
from ..storage import get_bytes, presigned_get, uri_to_key

router = APIRouter(tags=["distribution"])


def _watermark_image(data: bytes, text: str = "PREVIEW · DAM·AI") -> bytes:
    """Tile a semi-transparent watermark across a preview image so a shared,
    non-download link cannot be passed off as the clean original."""
    img = Image.open(io.BytesIO(data)).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    fsize = max(16, img.width // 24)
    try:
        font = ImageFont.load_default(size=fsize)
    except Exception:
        font = ImageFont.load_default()
    step_y, step_x = fsize * 5, fsize * 14
    for yi, y in enumerate(range(0, img.height, step_y)):
        offset = (yi % 2) * (step_x // 2)  # brick pattern
        for x in range(-step_x, img.width, step_x):
            draw.text((x + offset, y), text, fill=(255, 255, 255, 85), font=font)
    out = Image.alpha_composite(img, overlay).convert("RGB")
    buf = io.BytesIO()
    out.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


class ShareIn(BaseModel):
    scope_type: Literal["asset", "collection"]
    scope_id: str
    permission: Literal["view", "download", "edit", "admin"] = "view"
    expiry: datetime | None = None
    watermark: bool = False


class ShareOut(BaseModel):
    id: str
    token: str
    url: str
    scope_type: str
    scope_id: str
    permission: str
    expiry: datetime | None
    watermark: bool


@router.post("/api/shares", response_model=ShareOut)
async def create_share(
    body: ShareIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_role("distributor"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    token = secrets.token_urlsafe(24)
    share = Share(
        id=str(uuid.uuid4()), token=token, scope_type=body.scope_type, scope_id=body.scope_id,
        permission=body.permission, expiry=body.expiry, watermark_flag=body.watermark,
        created_by=user.id,
    )
    db.add(share)
    await db.commit()
    await audit.log(db, user.id, "share_create", body.scope_type, body.scope_id,
                    {"permission": body.permission, "expiry": str(body.expiry), "watermark": body.watermark})
    base = str(request.base_url).rstrip("/")
    return ShareOut(id=share.id, token=token, url=f"{base}/api/public/shares/{token}",
                    scope_type=body.scope_type, scope_id=body.scope_id, permission=body.permission,
                    expiry=body.expiry, watermark=body.watermark)


@router.get("/api/shares", response_model=list[ShareOut])
async def list_shares(request: Request, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    rows = (await db.execute(select(Share).where(Share.created_by == user.id)
                             .order_by(Share.created_at.desc()))).scalars().all()
    base = str(request.base_url).rstrip("/")
    return [ShareOut(id=s.id, token=s.token, url=f"{base}/api/public/shares/{s.token}",
                     scope_type=s.scope_type, scope_id=s.scope_id, permission=s.permission,
                     expiry=s.expiry, watermark=s.watermark_flag) for s in rows]


@router.delete("/api/shares/{share_id}")
async def revoke_share(share_id: str,
                       user: Annotated[CurrentUser, Depends(require_role("distributor"))],
                       db: Annotated[AsyncSession, Depends(get_db)]):
    s = (await db.execute(select(Share).where(Share.id == share_id))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Share not found")
    # Only the creator (or an administrator) may revoke — not any distributor.
    if s.created_by != user.id and ROLE_RANK.get(user.role, 0) < ROLE_RANK["administrator"]:
        raise HTTPException(403, "You can only revoke your own shares")
    await db.delete(s)
    await db.commit()
    await audit.log(db, user.id, "share_revoke", s.scope_type, s.scope_id, {})
    return {"status": "revoked"}


@router.get("/api/public/shares/{token}")
async def resolve_share(token: str, request: Request, db: Annotated[AsyncSession, Depends(get_db)]):
    """Public resolver — no auth. Honors expiry + permission; returns presigned URLs.
    Download permission serves the original; lower permissions serve a (watermarked)
    preview/thumbnail. Every access is audited (actor null = anonymous link)."""
    s = (await db.execute(select(Share).where(Share.token == token))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Invalid or expired link")
    if s.expiry and s.expiry < datetime.now(timezone.utc):
        raise HTTPException(410, "This link has expired")
    # A deactivated/removed creator's links must stop working (no orphaned access).
    creator = (await db.execute(select(AppUser).where(AppUser.id == s.created_by))).scalar_one_or_none()
    if not creator or not creator.is_active:
        raise HTTPException(410, "This link is no longer available")

    # gather assets in scope — EXCLUDE soft-deleted ones (a trashed asset must not keep
    # resolving through an old share link).
    if s.scope_type == "asset":
        assets = (await db.execute(select(Asset).where(
            Asset.id == s.scope_id, Asset.deleted_at.is_(None)))).scalars().all()
    else:
        assets = (await db.execute(
            select(Asset).join(CollectionItem, CollectionItem.asset_id == Asset.id)
            .where(CollectionItem.collection_id == s.scope_id, Asset.deleted_at.is_(None)))).scalars().all()

    allow_download = s.permission in ("download", "edit", "admin")
    base = str(request.base_url).rstrip("/")
    out = []
    for a in assets:
        watermarked = s.watermark_flag and not allow_download
        if allow_download:
            uri = a.storage_uri
            url = presigned_get(uri_to_key(uri)) if uri else None
        elif watermarked:
            # serve a RENDERED watermarked preview, never the clean original
            url = f"{base}/api/public/shares/{token}/wm/{a.id}"
        else:
            uri = a.thumbnail_uri or a.proxy_uri or a.storage_uri
            url = presigned_get(uri_to_key(uri)) if uri else None
        out.append({
            "id": a.id, "title": a.title or a.filename, "type": a.type,
            "url": url, "watermark": watermarked,
        })

    ip = request.client.host if request.client else None
    await audit.log(db, None, "share_access", s.scope_type, s.scope_id,
                    {"token": token, "permission": s.permission, "assets": len(out)}, ip=ip)
    return {"scope_type": s.scope_type, "permission": s.permission,
            "watermark": s.watermark_flag, "assets": out}


@router.get("/api/public/shares/{token}/wm/{asset_id}")
async def watermarked_preview(token: str, asset_id: str, db: Annotated[AsyncSession, Depends(get_db)]):
    """Render a watermarked preview for a non-download share. Validates the token,
    expiry, and that the asset is actually in the share's scope before serving."""
    s = (await db.execute(select(Share).where(Share.token == token))).scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Invalid or expired link")
    if s.expiry and s.expiry < datetime.now(timezone.utc):
        raise HTTPException(410, "This link has expired")
    if not s.watermark_flag or s.permission in ("download", "edit", "admin"):
        raise HTTPException(404, "No watermarked preview for this link")
    # asset must be within the share's scope
    if s.scope_type == "asset":
        in_scope = s.scope_id == asset_id
    else:
        in_scope = (await db.execute(select(CollectionItem).where(
            CollectionItem.collection_id == s.scope_id,
            CollectionItem.asset_id == asset_id))).scalar_one_or_none() is not None
    if not in_scope:
        raise HTTPException(404, "Asset not in share")
    a = (await db.execute(select(Asset).where(
        Asset.id == asset_id, Asset.deleted_at.is_(None)))).scalar_one_or_none()
    uri = (a.thumbnail_uri or a.proxy_uri or a.storage_uri) if a else None
    if not uri:
        raise HTTPException(404, "No preview available")
    try:
        raw = await asyncio.to_thread(get_bytes, uri_to_key(uri))
        wm = await asyncio.to_thread(_watermark_image, raw)
    except Exception:
        raise HTTPException(500, "Could not render preview")
    return Response(content=wm, media_type="image/jpeg",
                    headers={"Cache-Control": "private, max-age=300"})
