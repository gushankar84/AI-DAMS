"""Workflow engine (BRD §5.14): Uploaded → Under Review → Approved → Published → Archived,
with role-gated transitions and an auditable history."""
import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..db import get_db
from ..models import Asset, WorkflowStateLog
from ..security import CurrentUser, require_role

router = APIRouter(prefix="/api/assets", tags=["workflow"])

WF_STATES = ("uploaded", "under_review", "approved", "published", "archived")
# Transitions that assert editorial approval require reviewer+.
APPROVAL_STATES = {"approved", "published"}


class TransitionIn(BaseModel):
    state: Literal["uploaded", "under_review", "approved", "published", "archived"]
    note: str | None = None


class WorkflowEvent(BaseModel):
    state: str
    actor_id: str | None
    note: str | None
    created_at: str


@router.post("/{asset_id}/workflow")
async def transition(
    asset_id: str,
    body: TransitionIn,
    user: Annotated[CurrentUser, Depends(require_role("contributor"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    asset = (await db.execute(select(Asset).where(Asset.id == asset_id))).scalar_one_or_none()
    if not asset:
        raise HTTPException(404, "Asset not found")
    # Approving/publishing requires a reviewer or higher (BRD §9).
    from ..security import ROLE_RANK
    if body.state in APPROVAL_STATES and ROLE_RANK.get(user.role, 0) < ROLE_RANK["reviewer"]:
        raise HTTPException(403, f"Moving to '{body.state}' requires reviewer role or higher")

    asset.workflow = body.state
    db.add(WorkflowStateLog(id=str(uuid.uuid4()), asset_id=asset_id, state=body.state,
                            actor_id=user.id, note=body.note))
    await db.commit()
    await audit.log(db, user.id, "workflow", "asset", asset_id, {"state": body.state})
    return {"asset_id": asset_id, "state": body.state}


@router.get("/{asset_id}/workflow", response_model=list[WorkflowEvent])
async def history(asset_id: str, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    rows = (await db.execute(select(WorkflowStateLog).where(WorkflowStateLog.asset_id == asset_id)
                             .order_by(WorkflowStateLog.created_at))).scalars().all()
    return [WorkflowEvent(state=r.state, actor_id=r.actor_id, note=r.note,
                          created_at=r.created_at.isoformat()) for r in rows]
