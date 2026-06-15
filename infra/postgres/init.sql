-- ════════════════════════════════════════════════════════════════════════
-- DAM Platform — PostgreSQL schema (system of record)
-- Mirrors the Technical Solution Architecture §8 data model.
-- Frame-accuracy (§5) is a hard contract: temporal data carries frame_index +
-- PTS (rational, stored as numerator/denominator) + SMPTE + source fps/timebase.
--
-- Constrained-vocabulary columns use TEXT + CHECK rather than native ENUM types:
-- this keeps asyncpg (worker raw SQL) and SQLAlchemy (API) free of enum-cast
-- friction while preserving validation.
-- ════════════════════════════════════════════════════════════════════════

CREATE EXTENSION IF NOT EXISTS vector;       -- pgvector: single-vector / MVP workloads
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;       -- trigram fuzzy match for fallback search

-- ─── Users & RBAC ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS app_user (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  email         TEXT UNIQUE NOT NULL,
  display_name  TEXT NOT NULL,
  hashed_pw     TEXT NOT NULL,
  role          TEXT NOT NULL DEFAULT 'viewer'
                CHECK (role IN ('viewer','contributor','reviewer','distributor','administrator')),
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Assets ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS asset (
  id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  type          TEXT NOT NULL CHECK (type IN ('document','image','audio','video')),
  status        TEXT NOT NULL DEFAULT 'uploaded'
                CHECK (status IN ('uploaded','processing','extracting','indexed','searchable','failed')),
  title         TEXT,
  description   TEXT,
  filename      TEXT NOT NULL,
  mime_type     TEXT,
  size_bytes    BIGINT,
  checksum_sha256 TEXT,                       -- duplicate detection
  storage_uri   TEXT NOT NULL,                -- s3://bucket/key for the original
  proxy_uri     TEXT,                         -- streaming proxy (video/audio)
  thumbnail_uri TEXT,                         -- deterministic (non-generative) thumb
  -- standard metadata fields (BRD §5.12 / §7.3)
  tags          TEXT[] DEFAULT '{}',
  department    TEXT,
  project       TEXT,
  owner_id      UUID REFERENCES app_user(id),
  rights        TEXT,
  copyright     TEXT,
  expiry_date   DATE,
  language      TEXT,
  custom_meta   JSONB DEFAULT '{}'::jsonb,    -- per-department/project templates
  workflow      TEXT NOT NULL DEFAULT 'uploaded'
                CHECK (workflow IN ('uploaded','under_review','approved','published','archived')),
  error_detail  TEXT,
  deleted_at    TIMESTAMPTZ,                  -- soft-delete to Trash (FR-EXP-5)
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_asset_deleted ON asset(deleted_at);
CREATE INDEX IF NOT EXISTS idx_asset_type        ON asset(type);
CREATE INDEX IF NOT EXISTS idx_asset_status      ON asset(status);
CREATE INDEX IF NOT EXISTS idx_asset_checksum    ON asset(checksum_sha256);
CREATE INDEX IF NOT EXISTS idx_asset_tags        ON asset USING gin(tags);
CREATE INDEX IF NOT EXISTS idx_asset_custom_meta ON asset USING gin(custom_meta);

-- ─── Streams (per-asset media tracks; carry the authoritative frame map) ──
CREATE TABLE IF NOT EXISTS stream (
  id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  asset_id        UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  kind            TEXT NOT NULL CHECK (kind IN ('video','audio')),
  fps_num         INTEGER,                    -- e.g. 30000
  fps_den         INTEGER,                    -- e.g. 1001  -> 29.97
  duration_frames BIGINT,
  timebase        TEXT,                       -- ffprobe time_base, e.g. "1/30000"
  is_drop_frame   BOOLEAN NOT NULL DEFAULT FALSE,
  width           INTEGER,
  height          INTEGER,
  sample_rate     INTEGER,                    -- audio
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_stream_asset ON stream(asset_id);

-- ─── Persons (face identities; consent-gated, see §11) ────────────────────
CREATE TABLE IF NOT EXISTS person (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  display_name   TEXT,
  consent_status TEXT NOT NULL DEFAULT 'unknown'
                 CHECK (consent_status IN ('unknown','granted','denied','revoked')),
  notes          TEXT,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ─── Markers (every detection, frame-mapped). The canonical temporal record.
CREATE TABLE IF NOT EXISTS marker (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  asset_id     UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  stream_id    UUID REFERENCES stream(id) ON DELETE CASCADE,
  kind         TEXT NOT NULL CHECK (kind IN ('face','object','scene','speech','shot','activity','ocr')),
  frame_index  BIGINT,                        -- authoritative (NULL for non-temporal assets)
  end_frame    BIGINT,                        -- for ranged detections (shots/scenes/activities)
  pts_num      BIGINT,                        -- exact rational time: pts_num/pts_den seconds
  pts_den      BIGINT,
  smpte        TEXT,                          -- drop-frame aware, e.g. '01:12:14;05'
  fps_num      INTEGER,
  fps_den      INTEGER,
  label        TEXT,                          -- object/scene/activity label or matched keyword
  person_id    UUID REFERENCES person(id),    -- for face markers
  confidence   REAL,
  payload      JSONB DEFAULT '{}'::jsonb,     -- bbox, model name, extra attrs
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_marker_asset  ON marker(asset_id);
CREATE INDEX IF NOT EXISTS idx_marker_kind   ON marker(kind);
CREATE INDEX IF NOT EXISTS idx_marker_person ON marker(person_id);
CREATE INDEX IF NOT EXISTS idx_marker_frame  ON marker(asset_id, frame_index);

-- ─── Transcripts (timed segments for audio/video) ────────────────────────
CREATE TABLE IF NOT EXISTS transcript (
  id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  asset_id     UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  stream_id    UUID REFERENCES stream(id) ON DELETE CASCADE,
  start_frame  BIGINT NOT NULL,
  end_frame    BIGINT NOT NULL,
  start_pts_num BIGINT,
  start_pts_den BIGINT,
  speaker      TEXT,
  language     TEXT,
  text         TEXT NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_transcript_asset ON transcript(asset_id);

-- ─── Collections (virtual folders; reference assets, never duplicate) ─────
CREATE TABLE IF NOT EXISTS collection (
  id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  name        TEXT NOT NULL,
  description TEXT,
  owner_id    UUID REFERENCES app_user(id),
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS collection_item (
  collection_id UUID NOT NULL REFERENCES collection(id) ON DELETE CASCADE,
  asset_id      UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  added_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (collection_id, asset_id)
);

-- ─── Shares (distribution) ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS share (
  id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  token          TEXT UNIQUE NOT NULL,        -- public link token
  scope_type     TEXT NOT NULL,               -- 'asset' | 'collection'
  scope_id       UUID NOT NULL,
  permission     TEXT NOT NULL DEFAULT 'view'
                 CHECK (permission IN ('view','download','edit','admin')),
  expiry         TIMESTAMPTZ,
  watermark_flag BOOLEAN NOT NULL DEFAULT FALSE,
  created_by     UUID REFERENCES app_user(id),
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_share_token ON share(token);

-- ─── Workflow history (auditable state transitions) ───────────────────────
CREATE TABLE IF NOT EXISTS workflow_state (
  id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  asset_id   UUID NOT NULL REFERENCES asset(id) ON DELETE CASCADE,
  state      TEXT NOT NULL
             CHECK (state IN ('uploaded','under_review','approved','published','archived')),
  actor_id   UUID REFERENCES app_user(id),
  note       TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_wf_asset ON workflow_state(asset_id);

-- ─── Audit log (access, search, downloads, shares, admin actions — §11) ───
CREATE TABLE IF NOT EXISTS audit_log (
  id         BIGSERIAL PRIMARY KEY,
  actor_id   UUID REFERENCES app_user(id),
  action     TEXT NOT NULL,                   -- 'search' | 'view' | 'download' | 'share' | ...
  target_type TEXT,
  target_id  UUID,
  detail     JSONB DEFAULT '{}'::jsonb,
  ip         TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_actor  ON audit_log(actor_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
CREATE INDEX IF NOT EXISTS idx_audit_time   ON audit_log(created_at);

-- ─── updated_at trigger for asset ─────────────────────────────────────────
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS trigger AS $$
BEGIN NEW.updated_at = now(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_asset_touch ON asset;
CREATE TRIGGER trg_asset_touch BEFORE UPDATE ON asset
  FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- The API creates a bootstrap admin at startup using its own password hashing.
