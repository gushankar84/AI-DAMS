"""Pydantic request/response models for the API surface."""
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field

# ─── Auth ─────────────────────────────────────────────────────────────────
class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserOut(BaseModel):
    id: str
    email: str
    display_name: str
    role: str


class LoginIn(BaseModel):
    email: EmailStr
    password: str


# ─── Assets ───────────────────────────────────────────────────────────────
class AssetOut(BaseModel):
    id: str
    type: str
    status: str
    # Why processing failed — surfaced so a user can FIX the problem (corrupt file, OOM,
    # unsupported codec) instead of staring at a bare "failed" badge.
    error_detail: str | None = None
    title: str | None = None
    description: str | None = None
    filename: str
    mime_type: str | None = None
    size_bytes: int | None = None
    storage_uri: str
    proxy_uri: str | None = None
    thumbnail_uri: str | None = None
    tags: list[str] = []
    department: str | None = None
    project: str | None = None
    rights: str | None = None
    copyright: str | None = None
    expiry_date: date | None = None
    language: str | None = None
    workflow: str
    created_at: datetime

    class Config:
        from_attributes = True


class AssetUpdate(BaseModel):
    title: str | None = None
    description: str | None = None
    tags: list[str] | None = None
    department: str | None = None
    project: str | None = None
    rights: str | None = None
    copyright: str | None = None
    expiry_date: date | None = None
    language: str | None = None


class MarkerOut(BaseModel):
    id: str
    kind: str
    frame_index: int | None
    end_frame: int | None
    start_seconds: float | None = None   # for click-to-seek in the video viewer
    smpte: str | None
    label: str | None
    person_id: str | None
    confidence: float | None
    payload: dict[str, Any] = {}

    class Config:
        from_attributes = True


class TranscriptOut(BaseModel):
    id: str
    start_frame: int
    end_frame: int
    start_seconds: float | None = None   # for click-to-seek in the viewer
    speaker: str | None
    text: str

    class Config:
        from_attributes = True


class AssetDetail(AssetOut):
    markers: list[MarkerOut] = []
    transcript: list[TranscriptOut] = []


# ─── Search ───────────────────────────────────────────────────────────────
SearchSignal = Literal["keyword", "semantic", "multivector", "face", "object", "transcript"]


class SearchRequest(BaseModel):
    q: str = Field("", description="Natural-language or keyword query")
    types: list[str] | None = Field(None, description="Filter by asset type")
    department: str | None = None
    project: str | None = None
    tags: list[str] | None = None
    language: str | None = None
    date_from: date | None = None
    date_to: date | None = None
    limit: int = 24
    offset: int = 0
    sort: Literal["relevance", "date", "type"] = "relevance"
    rerank: bool = True   # cross-encoder precision stage over fused candidates (P4)
    min_score: float | None = Field(None, ge=0.0, le=1.0,
        description="Relevance floor for the dense-text signal (overrides the tuned default ~0.44). "
                    "Higher = stricter (less noise), lower = more recall. Set from the UI dial.")
    llm_refine: bool = False   # opt-in LLM relevance judge on the top-K of long queries
    # Modality intent: lean the search toward what was SAID / SEEN / WRITTEN. Set explicitly
    # by the UI chips, or auto-detected from phrasing ("talks about X" → spoken; "wearing X"
    # → visual). Soft — it reweights ranking, never excludes other modalities.
    intent: Literal["spoken", "visual", "written"] | None = None
                               # (experimental — slow on this box; see routers/search.py)


class TimelineHit(BaseModel):
    """An in-media match: asset + frame-mapped timestamp (BRD §5.6), or a document PAGE."""
    frame_index: int | None
    smpte: str | None
    kind: str
    label: str | None
    snippet: str | None = None
    page: int | None = None     # document hits: the page the match is on (PDF opens #page=N)


class SearchHit(BaseModel):
    asset_id: str
    type: str
    title: str | None
    filename: str
    thumbnail_uri: str | None
    score: float
    matched_signals: list[str] = []
    snippet: str | None = None
    caption: str | None = None           # VLM scene caption — shown on cards with no text snippet
    timeline: list[TimelineHit] = []     # for audio/video, the matching points inside
    created_at: datetime | None = None   # for date sort + display


class SearchResponse(BaseModel):
    query: str
    total: int
    took_ms: int
    hits: list[SearchHit]
    # Query decomposition (main→sub keyword cascade): [{term, role, idf, df}], broad→rare.
    # Empty for single-concept queries. Lets the UI show WHY/how the query was interpreted.
    concepts: list[dict] | None = None
    # Modality intent actually applied (explicit chip or auto-detected from phrasing) — echoed
    # so the UI can say "searching spoken content" transparently.
    intent: str | None = None
    # True when a query embedding call to the model server failed, so only keyword/BM25 ran
    # (semantic + visual paths dropped out). The UI shows a banner so results that silently
    # shrank aren't mistaken for "nothing matches".
    degraded: bool = False


# ─── Ingestion ────────────────────────────────────────────────────────────
class UploadResponse(BaseModel):
    asset_id: str
    status: str
    storage_uri: str
    message: str
