"""Asset ingestion + retrieval (Upload Center, Asset Explorer, Asset Viewer)."""
import hashlib
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Asset, CollectionItem, Marker, Transcript
from ..queue import enqueue_ingest
from ..schemas import AssetDetail, AssetOut, AssetUpdate, MarkerOut, TranscriptOut, UploadResponse
from ..security import CurrentUser, require_role
from ..storage import presigned_get, put_object, uri_to_key
from .. import audit

router = APIRouter(prefix="/api/assets", tags=["assets"])

# Map file extension -> asset type (BRD §7.1 supported formats).
EXT_TYPE = {
    "pdf": "document", "docx": "document", "xlsx": "document", "ppt": "document", "pptx": "document",
    "tiff": "image", "tif": "image", "jpg": "image", "jpeg": "image", "png": "image",
    "wav": "audio", "mp3": "audio",
    "mp4": "video", "mov": "video", "mxf": "video",
}


def _classify(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EXT_TYPE:
        raise HTTPException(415, f"Unsupported format '.{ext}'. Supported: {sorted(set(EXT_TYPE))}")
    return EXT_TYPE[ext]


@router.post("", response_model=UploadResponse)
async def upload(
    user: Annotated[CurrentUser, Depends(require_role("contributor"))],
    db: Annotated[AsyncSession, Depends(get_db)],
    file: UploadFile = File(...),
    title: str | None = Form(None),
    department: str | None = Form(None),
    project: str | None = Form(None),
):
    """Drag-and-drop ingestion. Stores the binary, records the asset, and queues
    AI processing. Status lifecycle: uploaded → processing → ... → searchable."""
    asset_type = _classify(file.filename)
    data = await file.read()
    checksum = hashlib.sha256(data).hexdigest()

    # Basic duplicate detection (FR-UPL-4)
    dupe = (await db.execute(select(Asset).where(Asset.checksum_sha256 == checksum))).scalar_one_or_none()
    if dupe:
        return UploadResponse(asset_id=dupe.id, status=dupe.status, storage_uri=dupe.storage_uri,
                              message="Duplicate of an existing asset (same checksum).")

    asset_id = str(uuid.uuid4())
    key = f"{asset_type}/{asset_id}/{file.filename}"
    storage_uri = put_object(key, data, file.content_type)

    asset = Asset(
        id=asset_id, type=asset_type, status="uploaded", title=title or file.filename,
        filename=file.filename, mime_type=file.content_type, size_bytes=len(data),
        checksum_sha256=checksum, storage_uri=storage_uri, department=department,
        project=project, owner_id=user.id, workflow="uploaded",
    )
    db.add(asset)
    await db.commit()
    await audit.log(db, user.id, "upload", "asset", asset_id, {"filename": file.filename})
    await enqueue_ingest(asset_id)
    return UploadResponse(asset_id=asset_id, status="uploaded", storage_uri=storage_uri,
                          message="Uploaded; AI processing queued.")


@router.get("", response_model=list[AssetOut])
async def list_assets(
    user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    type: str | None = None,
    status: str | None = None,
    workflow: str | None = None,
    collection_id: str | None = None,
    trashed: bool = False,
    limit: int = 50,
    offset: int = 0,
):
    """Asset Explorer listing with facets (BRD §5.3). Excludes Trash unless trashed=true."""
    stmt = select(Asset)
    stmt = stmt.where(Asset.deleted_at.isnot(None)) if trashed else stmt.where(Asset.deleted_at.is_(None))
    if type:
        stmt = stmt.where(Asset.type == type)
    if status:
        stmt = stmt.where(Asset.status == status)
    if workflow:
        stmt = stmt.where(Asset.workflow == workflow)
    if collection_id:
        stmt = stmt.join(CollectionItem, CollectionItem.asset_id == Asset.id).where(
            CollectionItem.collection_id == collection_id)
    stmt = stmt.order_by(Asset.created_at.desc()).limit(limit).offset(offset)
    rows = (await db.execute(stmt)).scalars().all()
    return [AssetOut.model_validate(a) for a in rows]


@router.delete("/{asset_id}")
async def soft_delete(asset_id: str,
                      user: Annotated[CurrentUser, Depends(require_role("contributor"))],
                      db: Annotated[AsyncSession, Depends(get_db)]):
    """Soft-delete to Trash (FR-EXP-5)."""
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    from datetime import datetime, timezone
    asset.deleted_at = datetime.now(timezone.utc)
    await db.commit()
    await audit.log(db, user.id, "delete", "asset", asset_id, {})
    return {"status": "trashed"}


@router.post("/{asset_id}/restore")
async def restore(asset_id: str,
                  user: Annotated[CurrentUser, Depends(require_role("contributor"))],
                  db: Annotated[AsyncSession, Depends(get_db)]):
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    asset.deleted_at = None
    await db.commit()
    await audit.log(db, user.id, "restore", "asset", asset_id, {})
    return {"status": "restored"}


@router.get("/{asset_id}", response_model=AssetDetail)
async def get_asset(asset_id: str, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    markers = (await db.execute(
        select(Marker).where(Marker.asset_id == asset_id).order_by(Marker.frame_index))).scalars().all()
    transcript = (await db.execute(
        select(Transcript).where(Transcript.asset_id == asset_id).order_by(Transcript.start_frame))).scalars().all()
    await audit.log(db, user.id, "view", "asset", asset_id, {})
    detail = AssetDetail.model_validate(asset)
    marker_out = []
    for m in markers:
        mo = MarkerOut.model_validate(m)
        if m.frame_index is not None and m.fps_num:
            mo.start_seconds = m.frame_index * (m.fps_den or 1) / m.fps_num
        marker_out.append(mo)
    detail.markers = marker_out
    transcript_out = []
    for t in transcript:
        o = TranscriptOut.model_validate(t)
        if t.start_pts_num and t.start_pts_den:
            o.start_seconds = t.start_pts_num / t.start_pts_den
        transcript_out.append(o)
    detail.transcript = transcript_out
    return detail


@router.get("/{asset_id}/media")
async def media_url(asset_id: str, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)],
                    variant: str = "original"):
    """Presigned, browser-reachable URL for the original / proxy / thumbnail."""
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    uri = {"original": asset.storage_uri, "proxy": asset.proxy_uri,
           "thumbnail": asset.thumbnail_uri}.get(variant, asset.storage_uri)
    if not uri:
        raise HTTPException(404, f"No {variant} available")
    return {"url": presigned_get(uri_to_key(uri))}


@router.get("/{asset_id}/text")
async def asset_text(asset_id: str, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    """Extracted document text for the in-app reader + find-in-document. PDFs render blank in
    some browsers' iframe plugins (and DOCX never render inline at all), so we serve the text we
    already extracted — searchable, find-able, and browser-independent. `pages` carries the
    per-page text when the doc was page-mapped (so a find hit can name its page)."""
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    from ..search import constants as C, qdrant_store
    from ..search.opensearch_store import client as os_client
    text = ""
    try:
        text = (os_client().get(index=C.OS_ASSETS, id=asset_id)["_source"] or {}).get("body") or ""
    except Exception:
        text = ""
    # Per-page reconstruction from the page-mapped dense chunks (ordered by page, then position).
    pages = None
    try:
        from qdrant_client.http import models as qm
        pts, _ = qdrant_store.client().scroll(
            collection_name=C.QDRANT_TEXT, limit=500, with_payload=True,
            scroll_filter=qm.Filter(must=[qm.FieldCondition(
                key="asset_id", match=qm.MatchValue(value=asset_id))]))
        by_page: dict[int, list] = {}
        for p in pts:
            pg = (p.payload or {}).get("page")
            if pg is not None:
                by_page.setdefault(int(pg), []).append(
                    (p.payload.get("chunk_index") or 0, p.payload.get("snippet") or ""))
        if by_page:
            pages = [{"page": pg, "text": " ".join(s for _, s in sorted(by_page[pg]))}
                     for pg in sorted(by_page)]
    except Exception:
        pages = None
    return {"text": text, "pages": pages, "filename": asset.filename, "type": asset.type}


@router.post("/{asset_id}/reprocess")
async def reprocess(
    asset_id: str,
    user: Annotated[CurrentUser, Depends(require_role("contributor"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Re-run the AI pipeline for an asset (FR-ADM-3 reprocess; NFR-A3 retryable)."""
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    asset.status = "uploaded"
    asset.error_detail = None
    await db.commit()
    await enqueue_ingest(asset_id)
    return {"status": "queued", "asset_id": asset_id}


@router.patch("/{asset_id}", response_model=AssetOut)
async def update_asset(
    asset_id: str,
    patch: AssetUpdate,
    user: Annotated[CurrentUser, Depends(require_role("contributor"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Review/correct AI-generated metadata (FR-META-3)."""
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    for field, value in patch.model_dump(exclude_unset=True).items():
        setattr(asset, field, value)
    await db.commit()
    await audit.log(db, user.id, "edit_metadata", "asset", asset_id,
                    patch.model_dump(exclude_unset=True, mode="json"))
    await db.refresh(asset)
    return AssetOut.model_validate(asset)
