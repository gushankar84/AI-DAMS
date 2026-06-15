"""Data-access for the worker: Postgres (asyncpg), Qdrant, OpenSearch, S3.

Uses raw SQL via asyncpg rather than re-importing the API's ORM, keeping the two
services decoupled. The schema is owned by infra/postgres/init.sql.
"""
from __future__ import annotations

import logging
import uuid

import asyncpg
import boto3
from botocore.client import Config
from opensearchpy import OpenSearch
from qdrant_client import QdrantClient
from qdrant_client.http import models as qm

from .config import (
    OS_ASSETS,
    OS_TRANSCRIPTS,
    QDRANT_FACE,
    QDRANT_IMAGE,
    QDRANT_TEXT,
    settings,
)

log = logging.getLogger("dam.stores")

_pg_pool: asyncpg.Pool | None = None


# ─── Postgres ──────────────────────────────────────────────────────────────
def _dsn() -> str:
    # asyncpg wants a plain postgres:// DSN, not the SQLAlchemy +asyncpg form.
    return settings.database_url.replace("postgresql+asyncpg://", "postgresql://")


async def pg() -> asyncpg.Pool:
    global _pg_pool
    if _pg_pool is None:
        _pg_pool = await asyncpg.create_pool(_dsn(), min_size=1, max_size=5)
    return _pg_pool


async def clear_derived(asset_id: str) -> None:
    """Remove all AI-derived data for an asset so re-ingestion is idempotent
    (NFR-A3). Asset row + binaries are untouched; the asset-level OpenSearch doc
    is keyed by asset_id and simply overwritten on re-index."""
    pool = await pg()
    await pool.execute("DELETE FROM transcript WHERE asset_id=$1", asset_id)
    # Persons the user NAMED or set consent on are identity WORK — reprocess must not wipe
    # them. Capture them; their face vectors are preserved below as re-link anchors so the
    # re-detected faces rejoin the SAME named identity instead of forming new unnamed clusters.
    named = [str(r["id"]) for r in await pool.fetch(
        "SELECT id FROM person WHERE display_name IS NOT NULL OR consent_status <> 'unknown'")]
    await pool.execute("DELETE FROM marker WHERE asset_id=$1", asset_id)
    await pool.execute("DELETE FROM stream WHERE asset_id=$1", asset_id)
    # Prune ONLY anonymous, unknown-consent empty clusters — NEVER a named/consented person.
    await pool.execute(
        "DELETE FROM person p WHERE p.display_name IS NULL AND p.consent_status = 'unknown' "
        "AND NOT EXISTS (SELECT 1 FROM marker m WHERE m.person_id = p.id)")
    # wait=True: the delete MUST finish before the pipeline re-inserts, else the async delete
    # races the upsert and leaves orphan vectors (NFR-A3).
    asset_flt = qm.Filter(must=[qm.FieldCondition(key="asset_id", match=qm.MatchValue(value=asset_id))])
    for col in (QDRANT_TEXT, QDRANT_IMAGE):
        try:
            qdrant().delete(collection_name=col, points_selector=qm.FilterSelector(filter=asset_flt), wait=True)
        except Exception as e:
            log.warning("clear_derived: failed to purge %s for asset %s: %s", col, asset_id, e)
    # Faces: purge this asset's face vectors EXCEPT named/consented persons' — those survive
    # as identity anchors so reprocess preserves the labels (re-detected faces re-link to them).
    face_flt = qm.Filter(
        must=[qm.FieldCondition(key="asset_id", match=qm.MatchValue(value=asset_id))],
        must_not=([qm.FieldCondition(key="person_id", match=qm.MatchAny(any=named))] if named else None))
    try:
        qdrant().delete(collection_name=QDRANT_FACE, points_selector=qm.FilterSelector(filter=face_flt), wait=True)
    except Exception as e:
        log.warning("clear_derived: failed to purge dam_face for asset %s: %s", asset_id, e)
    try:
        opensearch().delete_by_query(
            index=OS_TRANSCRIPTS, body={"query": {"term": {"asset_id": asset_id}}},
            refresh=True, conflicts="proceed")
    except Exception as e:
        # Don't swallow silently — a stale transcript left behind would surface as a ghost
        # search hit after reprocess. Logged so it's visible, not hidden.
        log.warning("clear_derived: failed to purge transcripts for asset %s: %s", asset_id, e)


async def set_status(asset_id: str, status: str, error: str | None = None) -> None:
    pool = await pg()
    await pool.execute(
        "UPDATE asset SET status=$2, error_detail=$3 WHERE id=$1", asset_id, status, error)


async def get_asset(asset_id: str) -> asyncpg.Record | None:
    pool = await pg()
    return await pool.fetchrow("SELECT * FROM asset WHERE id=$1", asset_id)


async def set_description(asset_id: str, text: str) -> None:
    """Store the generated asset summary as the description (shown in the asset detail UI).
    Only fills an EMPTY description — never overwrites a human-written one."""
    pool = await pg()
    await pool.execute(
        "UPDATE asset SET description=$2 WHERE id=$1 AND (description IS NULL OR description='')",
        asset_id, text)


async def set_asset_text(asset_id: str, title: str | None, language: str | None) -> None:
    pool = await pg()
    if title:
        await pool.execute(
            "UPDATE asset SET title=COALESCE(title,$2), language=COALESCE($3,language) WHERE id=$1",
            asset_id, title, language)


async def create_stream(asset_id: str, kind: str, fps_num, fps_den, duration_frames,
                        timebase, is_drop_frame, width=None, height=None, sample_rate=None) -> str:
    pool = await pg()
    sid = str(uuid.uuid4())
    await pool.execute(
        """INSERT INTO stream(id,asset_id,kind,fps_num,fps_den,duration_frames,timebase,
           is_drop_frame,width,height,sample_rate)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)""",
        sid, asset_id, kind, fps_num, fps_den, duration_frames, timebase,
        is_drop_frame, width, height, sample_rate)
    return sid


async def create_person() -> str:
    pool = await pg()
    pid = str(uuid.uuid4())
    await pool.execute("INSERT INTO person(id, consent_status) VALUES($1,'unknown')", pid)
    return pid


def face_nearest(embedding: list[float]) -> tuple[str | None, float]:
    """Nearest existing face in dam_face -> (person_id, cosine score). Drives
    name-once clustering: a new face that matches an existing person above the
    threshold inherits that identity; otherwise a new person is created."""
    try:
        res = qdrant().query_points(QDRANT_FACE, query=embedding, limit=1, with_payload=True).points
        if res and res[0].payload:
            return res[0].payload.get("person_id"), float(res[0].score)
    except Exception:
        pass
    return None, 0.0


async def insert_markers(rows: list[dict]) -> None:
    if not rows:
        return
    pool = await pg()
    await pool.executemany(
        """INSERT INTO marker(id,asset_id,stream_id,kind,frame_index,end_frame,pts_num,pts_den,
           smpte,fps_num,fps_den,label,person_id,confidence,payload)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15::jsonb)""",
        [(r.get("id", str(uuid.uuid4())), r["asset_id"], r.get("stream_id"), r["kind"],
          r.get("frame_index"), r.get("end_frame"), r.get("pts_num"), r.get("pts_den"),
          r.get("smpte"), r.get("fps_num"), r.get("fps_den"), r.get("label"),
          r.get("person_id"), r.get("confidence"), _json(r.get("payload", {}))) for r in rows])


async def insert_transcripts(rows: list[dict]) -> None:
    if not rows:
        return
    pool = await pg()
    await pool.executemany(
        """INSERT INTO transcript(id,asset_id,stream_id,start_frame,end_frame,start_pts_num,
           start_pts_den,speaker,language,text)
           VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
        [(str(uuid.uuid4()), r["asset_id"], r.get("stream_id"), r["start_frame"], r["end_frame"],
          r.get("start_pts_num"), r.get("start_pts_den"), r.get("speaker"),
          r.get("language"), r["text"]) for r in rows])


def _json(obj) -> str:
    import json
    return json.dumps(obj)


# ─── Qdrant ──────────────────────────────────────────────────────────────
_qdrant: QdrantClient | None = None


def qdrant() -> QdrantClient:
    global _qdrant
    if _qdrant is None:
        _qdrant = QdrantClient(url=settings.qdrant_url, api_key=settings.qdrant_api_key or None, timeout=30)
    return _qdrant


def upsert_vectors(collection: str, points: list[dict]) -> None:
    """points: [{id, vector, payload}]. Upserts in BATCHES — a single request must stay
    under Qdrant's 33 MB payload cap (a large PDF can produce thousands of chunks; one
    giant upsert previously 400'd at ~114 MB and failed the whole ingest)."""
    if not points:
        return
    structs = [qm.PointStruct(id=p["id"], vector=p["vector"], payload=p["payload"]) for p in points]
    BATCH = 256  # ~256 * (1024-dim vector + small payload) ≈ a few MB per request
    c = qdrant()
    for i in range(0, len(structs), BATCH):
        c.upsert(collection_name=collection, points=structs[i:i + BATCH], wait=True)


# ─── OpenSearch ──────────────────────────────────────────────────────────
_os: OpenSearch | None = None


def opensearch() -> OpenSearch:
    global _os
    if _os is None:
        auth = (settings.opensearch_user, settings.opensearch_password or "") if settings.opensearch_user else None
        _os = OpenSearch(hosts=[settings.opensearch_url], http_auth=auth, timeout=30)
    return _os


def index_asset_doc(doc: dict) -> None:
    opensearch().index(index=OS_ASSETS, id=doc["asset_id"], body=doc, refresh=True)


def index_transcript_segments(segments: list[dict]) -> None:
    if not segments:
        return
    from opensearchpy import helpers
    helpers.bulk(opensearch(), [
        {"_index": OS_TRANSCRIPTS, "_source": s} for s in segments])


# ─── S3 ──────────────────────────────────────────────────────────────────
_s3_client = None


def _s3():
    # Cached: building a fresh Session+client (new connection pool) on every download/upload
    # leaked sockets under load. boto3 clients are thread-safe for these calls.
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.session.Session().client(
            "s3", endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key, aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region, config=Config(signature_version="s3v4"),
            use_ssl=settings.s3_secure)
    return _s3_client


def download_to(storage_uri: str, dest_path: str) -> str:
    prefix = f"s3://{settings.s3_bucket}/"
    key = storage_uri[len(prefix):] if storage_uri.startswith(prefix) else storage_uri
    _s3().download_file(settings.s3_bucket, key, dest_path)
    return dest_path


def upload_file(local_path: str, key: str, content_type: str | None = None) -> str:
    extra = {"ContentType": content_type} if content_type else {}
    _s3().upload_file(local_path, settings.s3_bucket, key, ExtraArgs=extra or None)
    return f"s3://{settings.s3_bucket}/{key}"


def get_text(key: str) -> str | None:
    """Read a small text object (OCR page checkpoint). None if absent."""
    try:
        return _s3().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read().decode("utf-8")
    except Exception:
        return None


def put_text(key: str, text: str) -> None:
    """Persist a small text object (OCR page checkpoint) — survives crashes/reboots, so a
    re-run resumes instead of redoing finished pages."""
    _s3().put_object(Bucket=settings.s3_bucket, Key=key, Body=text.encode("utf-8"),
                     ContentType="text/plain; charset=utf-8")
