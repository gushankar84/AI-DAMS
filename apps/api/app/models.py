"""ORM models mapping the Postgres schema (see infra/postgres/init.sql).

Enum columns are mapped as plain strings — Postgres enforces the enum type, and
asyncpg casts text to the enum on write, so we avoid SQLAlchemy trying to manage
the PG enum types itself.
"""
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class AppUser(Base):
    __tablename__ = "app_user"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    email: Mapped[str] = mapped_column(Text, unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    hashed_pw: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String, default="viewer")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Asset(Base):
    __tablename__ = "asset"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="uploaded")
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[str | None] = mapped_column(Text, nullable=True)
    storage_uri: Mapped[str] = mapped_column(Text)
    proxy_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail_uri: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str]] = mapped_column(ARRAY(Text), default=list)
    department: Mapped[str | None] = mapped_column(Text, nullable=True)
    project: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("app_user.id"), nullable=True)
    rights: Mapped[str | None] = mapped_column(Text, nullable=True)
    copyright: Mapped[str | None] = mapped_column(Text, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    workflow: Mapped[str] = mapped_column(String, default="uploaded")
    error_detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Stream(Base):
    __tablename__ = "stream"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("asset.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String)
    fps_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps_den: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_frames: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    timebase: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_drop_frame: Mapped[bool] = mapped_column(Boolean, default=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sample_rate: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Person(Base):
    __tablename__ = "person"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_status: Mapped[str] = mapped_column(String, default="unknown")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Marker(Base):
    __tablename__ = "marker"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("asset.id", ondelete="CASCADE"))
    stream_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("stream.id", ondelete="CASCADE"), nullable=True)
    kind: Mapped[str] = mapped_column(String)
    frame_index: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    end_frame: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pts_num: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    pts_den: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    smpte: Mapped[str | None] = mapped_column(Text, nullable=True)
    fps_num: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fps_den: Mapped[int | None] = mapped_column(Integer, nullable=True)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    person_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("person.id"), nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Transcript(Base):
    __tablename__ = "transcript"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("asset.id", ondelete="CASCADE"))
    stream_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("stream.id", ondelete="CASCADE"), nullable=True)
    start_frame: Mapped[int] = mapped_column(BigInteger)
    end_frame: Mapped[int] = mapped_column(BigInteger)
    start_pts_num: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    start_pts_den: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    speaker: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Collection(Base):
    __tablename__ = "collection"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("app_user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CollectionItem(Base):
    __tablename__ = "collection_item"
    collection_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("collection.id", ondelete="CASCADE"), primary_key=True)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("asset.id", ondelete="CASCADE"), primary_key=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Share(Base):
    __tablename__ = "share"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    token: Mapped[str] = mapped_column(Text, unique=True)
    scope_type: Mapped[str] = mapped_column(Text)
    scope_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    permission: Mapped[str] = mapped_column(String, default="view")
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    watermark_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("app_user.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WorkflowStateLog(Base):
    __tablename__ = "workflow_state"
    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    asset_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("asset.id", ondelete="CASCADE"))
    state: Mapped[str] = mapped_column(String)
    actor_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("app_user.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    actor_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), ForeignKey("app_user.id"), nullable=True)
    action: Mapped[str] = mapped_column(Text)
    target_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, default=dict)
    ip: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
