"""Person identities & consent governance (BRD §5.7 FR-FACE-3 / NFR-S4).

Naming face clusters improves recognition; consent status gates whether a person
may surface in facial search. Facial recognition is the highest-sensitivity
capability — all changes here are audited.
"""
import os
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .. import audit
from ..config import settings
from ..db import get_db
from ..models import Asset, Marker, Person
from ..search.constants import OS_ASSETS
from ..search.opensearch_store import client as os_client
from ..security import CurrentUser, require_role
from ..storage import presigned_get

router = APIRouter(prefix="/api/persons", tags=["persons"])
_WORKER = os.environ.get("AI_WORKER_URL", "http://127.0.0.1:8100").replace("localhost", "127.0.0.1")


def _face_thumb_key(person_id: str) -> str:
    return f"face-thumbs/{person_id}.jpg"


async def _best_face(db: AsyncSession, person_id: str):
    """The highest-confidence face of a person + its asset, to crop an avatar from."""
    row = (await db.execute(
        select(Marker, Asset).join(Asset, Asset.id == Marker.asset_id)
        .where(Marker.person_id == person_id, Marker.kind == "face",
               Asset.deleted_at.is_(None))
        .order_by(Marker.confidence.desc().nullslast()).limit(1))).first()
    return row  # (Marker, Asset) | None


async def _reindex_person_names(db: AsyncSession, person_id: str) -> int:
    """Push named people into the searchable `entities` field of every asset their
    face appears in, so a text search for the name finds the clip (FR-FACE: search
    by name). Sets each affected asset's entities to ALL named persons in it."""
    asset_ids = (await db.execute(select(Marker.asset_id).where(
        Marker.person_id == person_id, Marker.kind == "face").distinct())).scalars().all()
    for aid in asset_ids:
        names = (await db.execute(
            select(Person.display_name).join(Marker, Marker.person_id == Person.id)
            .where(Marker.asset_id == aid, Marker.kind == "face",
                   Person.display_name.isnot(None)).distinct())).scalars().all()
        joined = " ".join(sorted({n for n in names if n}))
        try:
            os_client().update(index=OS_ASSETS, id=str(aid), body={"doc": {"entities": joined}}, refresh=True)
        except Exception:
            pass
    return len(asset_ids)


class PersonOut(BaseModel):
    id: str
    display_name: str | None
    consent_status: str
    face_count: int
    thumb_url: str | None = None   # presigned face-crop avatar (lazily generated)


class PersonPatch(BaseModel):
    display_name: str | None = None
    consent_status: Literal["unknown", "granted", "denied", "revoked"] | None = None


@router.get("", response_model=list[PersonOut])
async def list_persons(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)], limit: int = 200):
    rows = (await db.execute(select(Person).limit(limit))).scalars().all()
    counts = dict((await db.execute(
        select(Marker.person_id, func.count()).where(Marker.kind == "face").group_by(Marker.person_id))).all())
    out = [PersonOut(id=p.id, display_name=p.display_name, consent_status=p.consent_status,
                     face_count=int(counts.get(p.id, 0)),
                     thumb_url=presigned_get(_face_thumb_key(p.id)))
           for p in rows if counts.get(p.id, 0) > 0]
    out.sort(key=lambda x: x.face_count, reverse=True)  # most-appearing people first
    return out


@router.post("/{person_id}/face")
async def generate_face_thumb(person_id: str, user: CurrentUser,
                              db: Annotated[AsyncSession, Depends(get_db)]):
    """Lazily crop this person's representative face into an avatar (cached in S3).
    The UI calls this when a thumbnail is missing, so naming isn't blind."""
    row = await _best_face(db, person_id)
    if not row:
        raise HTTPException(404, "no face for this person")
    marker, asset = row
    bbox = (marker.payload or {}).get("bbox")
    if not bbox:
        raise HTTPException(404, "no face bbox stored")
    # crop source: the original image, or — for video — the shot keyframe the face was found in
    if asset.type == "video" and marker.frame_index is not None:
        storage_uri = f"s3://{settings.s3_bucket}/video/{asset.id}/keyframes/{marker.frame_index}.jpg"
        filename = f"{marker.frame_index}.jpg"
    else:
        storage_uri, filename = asset.storage_uri, asset.filename
    out_key = _face_thumb_key(person_id)
    try:
        r = httpx.post(f"{_WORKER}/face-crop", timeout=60, json={
            "storage_uri": storage_uri, "filename": filename, "bbox": bbox, "out_key": out_key})
        r.raise_for_status()
    except Exception as e:
        raise HTTPException(503, f"face crop failed: {e}")
    return {"thumb_url": presigned_get(out_key)}


@router.patch("/{person_id}", response_model=PersonOut)
async def update_person(
    person_id: str,
    patch: PersonPatch,
    user: Annotated[CurrentUser, Depends(require_role("contributor"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    p = (await db.execute(select(Person).where(Person.id == person_id))).scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Person not found")
    # Consent changes are a governance action -> reviewer or higher.
    if patch.consent_status is not None:
        from ..security import ROLE_RANK
        if ROLE_RANK.get(user.role, 0) < ROLE_RANK["reviewer"]:
            raise HTTPException(403, "Changing consent status requires reviewer role or higher")
        p.consent_status = patch.consent_status
    if patch.display_name is not None:
        name = patch.display_name.strip()
        if name:
            # Names are unique identities — reject a label already used by ANOTHER person.
            # The UI catches this first (offering merge / a "Nani2" suggestion); this is the
            # backend safety net so the data can never hold two people with the same name.
            dup = (await db.execute(select(Person.id).where(
                func.lower(Person.display_name) == name.lower(), Person.id != person_id))).first()
            if dup:
                raise HTTPException(409, f"'{name}' is already used by another person — "
                                         "merge them or choose a unique name (e.g. add a number).")
        p.display_name = name
    await db.commit()
    # Naming a person makes them findable by name in universal search.
    if patch.display_name is not None:
        await _reindex_person_names(db, person_id)
    await audit.log(db, user.id, "person_update", "person", person_id,
                    patch.model_dump(exclude_unset=True))
    cnt = (await db.execute(select(func.count()).where(
        Marker.kind == "face", Marker.person_id == person_id))).scalar() or 0
    return PersonOut(id=p.id, display_name=p.display_name, consent_status=p.consent_status, face_count=int(cnt))


class MergeIn(BaseModel):
    source_ids: list[str]


@router.post("/{target_id}/merge")
async def merge_persons(
    target_id: str,
    body: MergeIn,
    user: Annotated[CurrentUser, Depends(require_role("reviewer"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Merge duplicate identity clusters into ONE person. Re-points every face (markers
    + face vectors) from the sources to the target, then deletes the empty sources. Use
    when the clusterer split one real person across several rows. Reviewer-gated because it
    moves faces across consent boundaries (same as editing consent)."""
    target = (await db.execute(select(Person).where(Person.id == target_id))).scalar_one_or_none()
    if not target:
        raise HTTPException(404, "Target person not found")
    sources = [s for s in dict.fromkeys(body.source_ids) if s != target_id]
    if not sources:
        raise HTTPException(400, "No source persons to merge")
    # Consent must NEVER relax on merge: take the MOST-restrictive status across target +
    # sources, so a denied/revoked person's faces can't become searchable via the target.
    pool = set((await db.execute(select(Person.consent_status).where(
        Person.id.in_(sources)))).scalars().all()) | {target.consent_status}
    if "denied" in pool:
        target.consent_status = "denied"
    elif "revoked" in pool:
        target.consent_status = "revoked"
    # 1) re-point face markers (Postgres) — drives face_count + the consent join
    await db.execute(update(Marker).where(Marker.person_id.in_(sources)).values(person_id=target_id))
    # 2) re-point face VECTORS (Qdrant) so face search + consent gating still resolve
    try:
        from qdrant_client.http import models as qm
        from ..search.constants import QDRANT_FACE
        from ..search.qdrant_store import client as qclient
        qclient().set_payload(
            collection_name=QDRANT_FACE, payload={"person_id": target_id}, wait=True,
            points=qm.Filter(must=[qm.FieldCondition(key="person_id", match=qm.MatchAny(any=sources))]))
    except Exception:
        pass  # best-effort; markers remain the source of truth
    # 3) delete the now-empty source rows
    await db.execute(delete(Person).where(Person.id.in_(sources)))
    await db.commit()
    await _reindex_person_names(db, target_id)  # all those faces now belong to the target
    await audit.log(db, user.id, "person_merge", "person", target_id, {"sources": sources})
    cnt = (await db.execute(select(func.count()).where(
        Marker.kind == "face", Marker.person_id == target_id))).scalar() or 0
    return {"merged": len(sources), "into": target_id, "face_count": int(cnt)}


@router.post("/{person_id}/split")
async def split_person(
    person_id: str,
    user: Annotated[CurrentUser, Depends(require_role("reviewer"))],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Split an OVER-merged cluster (two people grouped as one identity) into two — the
    inverse of merge. k=2 clusters the face vectors; the smaller group moves to a NEW
    person. Reviewer-gated (moves faces across consent boundaries). Re-merge if wrong."""
    import uuid as _uuid
    import numpy as np
    from qdrant_client.http import models as qm
    from ..search.constants import QDRANT_FACE
    from ..search.qdrant_store import client as qclient
    parent = (await db.execute(select(Person).where(Person.id == person_id))).scalar_one_or_none()
    if not parent:
        raise HTTPException(404, "Person not found")
    c = qclient()
    pts, offset = [], None
    while True:
        batch, offset = c.scroll(
            collection_name=QDRANT_FACE, with_vectors=True, limit=256, offset=offset,
            scroll_filter=qm.Filter(must=[qm.FieldCondition(
                key="person_id", match=qm.MatchValue(value=person_id))]))
        pts += batch
        if offset is None:
            break
    if len(pts) < 4:
        raise HTTPException(400, "Need at least 4 faces to split a cluster.")
    ids = [p.id for p in pts]
    Xn = np.asarray([p.vector for p in pts], dtype=float)
    Xn = Xn / (np.linalg.norm(Xn, axis=1, keepdims=True) + 1e-9)
    a = int(((Xn - Xn[0]) ** 2).sum(1).argmax())          # two most-distant faces as seeds
    b = int(((Xn - Xn[a]) ** 2).sum(1).argmax())
    cent = np.array([Xn[a], Xn[b]])
    assign = (Xn @ cent.T).argmax(1)
    for _ in range(15):                                    # cosine k=2 (Lloyd iterations)
        assign = (Xn @ cent.T).argmax(1)
        for k in (0, 1):
            if (assign == k).any():
                m = Xn[assign == k].mean(0)
                cent[k] = m / (np.linalg.norm(m) + 1e-9)
    small = 0 if int((assign == 0).sum()) <= int((assign == 1).sum()) else 1
    move = [ids[i] for i in range(len(ids)) if assign[i] == small]
    if not move or len(move) == len(ids):
        raise HTTPException(400, "Could not separate two distinct groups.")
    new_id = str(_uuid.uuid4())
    db.add(Person(id=new_id, consent_status=parent.consent_status))  # inherit consent, don't reset
    await db.flush()
    # point id == marker id (set at enrich time), so we can re-point markers directly
    await db.execute(update(Marker).where(Marker.id.in_(move)).values(person_id=new_id))
    await db.commit()
    try:
        c.set_payload(collection_name=QDRANT_FACE, payload={"person_id": new_id}, points=move, wait=True)
    except Exception:
        pass
    await audit.log(db, user.id, "person_split", "person", person_id, {"new": new_id, "moved": len(move)})
    return {"new_person": new_id, "moved": len(move), "remaining": len(ids) - len(move)}


@router.get("/suggestions")
async def merge_suggestions(user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    """For each identity, the most-similar OTHER identity by face embedding — clusters the
    auto-grouper left separate but that are likely the SAME person. Powers the 'looks like…'
    merge hint and name auto-fill (when the look-alike is already named). Computed on demand."""
    from qdrant_client.http import models as qm
    from ..search import qdrant_store
    from ..search.constants import QDRANT_FACE
    persons = (await db.execute(select(Person))).scalars().all()
    named = {p.id: p.display_name for p in persons if p.display_name}
    c = qdrant_store.client()
    out, seen = [], set()
    for p in persons:
        pts, _ = c.scroll(collection_name=QDRANT_FACE, with_vectors=True, limit=1,
                          scroll_filter=qm.Filter(must=[qm.FieldCondition(
                              key="person_id", match=qm.MatchValue(value=p.id))]))
        if not pts or pts[0].vector is None:
            continue
        for h in qdrant_store.search(QDRANT_FACE, pts[0].vector, limit=12, score_threshold=0.45):
            other = (h.get("payload") or {}).get("person_id")
            if other and other != p.id:
                key = tuple(sorted([p.id, other]))
                if key not in seen:
                    seen.add(key)
                    out.append({"person_id": p.id, "similar_to": other,
                                "score": round(h["score"], 3), "suggested_name": named.get(other)})
                break
    # NOTE: identity = FACE, not name. We deliberately do NOT pair clusters just because
    # they share a name — two different people can both be "Nani". Only face similarity
    # (above) drives merge suggestions; the name is only surfaced as a hint when the
    # face-matched look-alike happens to be named.
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


@router.get("/{person_id}/assets")
async def person_assets(person_id: str, user: CurrentUser, db: Annotated[AsyncSession, Depends(get_db)]):
    """Every asset this person's face appears in, each with a CROPPED FACE thumbnail (not
    the full frame) so you can verify identity for naming/merging. Crops are cached."""
    from ..storage import object_exists
    rows = (await db.execute(
        select(Marker, Asset).join(Asset, Asset.id == Marker.asset_id)
        .where(Marker.person_id == person_id, Marker.kind == "face", Asset.deleted_at.is_(None))
        .order_by(Marker.confidence.desc().nullslast()))).all()
    out, seen = [], set()
    for marker, asset in rows:
        if asset.id in seen:
            continue
        seen.add(asset.id)
        bbox = (marker.payload or {}).get("bbox")
        face_url = None
        if bbox:
            key = f"face-crops/{person_id}/{asset.id}.jpg"
            if not object_exists(key):
                if asset.type == "video" and marker.frame_index is not None:
                    suri = f"s3://{settings.s3_bucket}/video/{asset.id}/keyframes/{marker.frame_index}.jpg"
                    fn = f"{marker.frame_index}.jpg"
                else:
                    suri, fn = asset.storage_uri, asset.filename
                try:
                    httpx.post(f"{_WORKER}/face-crop", timeout=60, json={
                        "storage_uri": suri, "filename": fn, "bbox": bbox, "out_key": key})
                except Exception:
                    pass
            if object_exists(key):
                face_url = presigned_get(key)
        out.append({"id": asset.id, "filename": asset.filename, "type": asset.type, "face_url": face_url})
    return out


async def denied_person_ids(db: AsyncSession) -> set[str]:
    """Persons whose consent is denied/revoked — excluded from facial search (NFR-S4)."""
    rows = (await db.execute(select(Person.id).where(
        Person.consent_status.in_(("denied", "revoked"))))).scalars().all()
    return set(rows)
