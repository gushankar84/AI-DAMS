"""Collections (BRD §5.10) — virtual folders grouping mixed assets by reference."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import Asset, Collection, CollectionItem
from ..schemas import AssetOut
from ..security import CurrentUser, require_role

router = APIRouter(prefix="/api/collections", tags=["collections"])


class CollectionIn(BaseModel):
    name: str
    description: str | None = None


class CollectionOut(BaseModel):
    id: str
    name: str
    description: str | None
    item_count: int = 0


@router.post("", response_model=CollectionOut)
async def create(body: CollectionIn,
                 user: Annotated[CurrentUser, Depends(require_role("contributor"))],
                 db: Annotated[AsyncSession, Depends(get_db)]):
    col = Collection(id=str(uuid.uuid4()), name=body.name, description=body.description, owner_id=user.id)
    db.add(col)
    await db.commit()
    return CollectionOut(id=col.id, name=col.name, description=col.description, item_count=0)


@router.get("", response_model=list[CollectionOut])
async def list_collections(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    rows = (await db.execute(select(Collection).order_by(Collection.created_at.desc()))).scalars().all()
    counts = dict((await db.execute(
        select(CollectionItem.collection_id, func.count()).group_by(CollectionItem.collection_id))).all())
    return [CollectionOut(id=c.id, name=c.name, description=c.description,
                          item_count=int(counts.get(c.id, 0))) for c in rows]


@router.get("/{collection_id}")
async def get_collection(collection_id: str, user: CurrentUser,
                         db: Annotated[AsyncSession, Depends(get_db)]):
    col = (await db.execute(select(Collection).where(Collection.id == collection_id))).scalar_one_or_none()
    if not col:
        raise HTTPException(404, "Collection not found")
    assets = (await db.execute(
        select(Asset).join(CollectionItem, CollectionItem.asset_id == Asset.id)
        .where(CollectionItem.collection_id == collection_id, Asset.deleted_at.is_(None))
        .order_by(CollectionItem.added_at.desc()))).scalars().all()
    return {"id": col.id, "name": col.name, "description": col.description,
            "assets": [AssetOut.model_validate(a) for a in assets]}


@router.post("/{collection_id}/items/{asset_id}")
async def add_item(collection_id: str, asset_id: str,
                   user: Annotated[CurrentUser, Depends(require_role("contributor"))],
                   db: Annotated[AsyncSession, Depends(get_db)]):
    exists = (await db.execute(select(Collection).where(Collection.id == collection_id))).scalar_one_or_none()
    if not exists:
        raise HTTPException(404, "Collection not found")
    asset = (await db.execute(select(Asset).where(
        Asset.id == asset_id, Asset.deleted_at.is_(None)))).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    # Idempotent: re-adding an asset already in the collection is a no-op (avoids a PK
    # IntegrityError → 500). A bad/missing asset_id is now a clean 404, not an FK 500.
    already = (await db.execute(select(CollectionItem).where(
        CollectionItem.collection_id == collection_id,
        CollectionItem.asset_id == asset_id))).scalar_one_or_none()
    if already:
        return {"status": "exists"}
    db.add(CollectionItem(collection_id=collection_id, asset_id=asset_id))
    await db.commit()
    return {"status": "added"}


@router.delete("/{collection_id}/items/{asset_id}")
async def remove_item(collection_id: str, asset_id: str,
                      user: Annotated[CurrentUser, Depends(require_role("contributor"))],
                      db: Annotated[AsyncSession, Depends(get_db)]):
    await db.execute(delete(CollectionItem).where(
        CollectionItem.collection_id == collection_id, CollectionItem.asset_id == asset_id))
    await db.commit()
    return {"status": "removed"}
