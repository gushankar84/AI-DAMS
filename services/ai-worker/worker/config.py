"""AI-worker settings. Vector dims MUST match apps/api/app/search/constants.py."""
import os
from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Populate os.environ from the nearest .env (walks up to repo root) so that
# libraries reading the environment directly — notably HuggingFace's HF_HOME /
# TORCH_HOME — see them. pydantic-settings alone only loads into Settings, not
# into os.environ.
load_dotenv()

# Reduce CUDA allocator fragmentation (helps on Windows WDDM where GPU memory is
# commit-backed). Harmless on CPU.
os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = "postgresql+asyncpg://dam:dam_dev_pw@localhost:5432/dam"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    opensearch_url: str = "http://localhost:9200"
    opensearch_user: str | None = None
    opensearch_password: str | None = None

    s3_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "dam-assets"
    s3_region: str = "us-east-1"
    s3_secure: bool = False

    # ── Models ────────────────────────────────────────────────────────────
    # Resident (always loaded, latency-critical): text + image embedders + reranker.
    # On-demand (loaded then evicted to free VRAM): ASR, detection, faces, VLM.
    ai_device: str = "cuda"            # 'cuda' | 'cpu'
    text_embed_model: str = "BAAI/bge-m3"                  # RESIDENT — multilingual text
    rerank_model: str = "BAAI/bge-reranker-v2-m3"          # RESIDENT — multilingual reranker
    image_embed_model: str = "google/siglip2-base-patch16-512"  # RESIDENT — SigLIP 2 (transformers); multilingual image
    asr_model: str = "large-v3"                            # ON-DEMAND — Whisper, released after use
    # int8_float16 halves Whisper VRAM (~3GB → ~2GB) with negligible accuracy loss — small
    # enough to CO-RESIDE with the 7GB VLM on the 20GB GPU. This kills the old swap dance
    # (evict VLM → load Whisper → cold-reload VLM) that wasted 60-90s per video and crashed
    # the server when the eviction didn't finish in time.
    asr_compute_type: str = "int8_float16"
    # ADAPTIVE guard instead of a schedule: before a GPU load, ASR checks actual free VRAM and
    # silently falls back to CPU for that call if the user's own GPU jobs have taken the room.
    asr_min_free_vram_gb: float = 3.5
    # Hard override: force CPU ASR always (bulletproof when heavy external GPU jobs run 24/7).
    asr_force_cpu: bool = False
    # Voice-activity filter: trims non-speech so Whisper doesn't hallucinate on
    # silence. BUT the default Silero threshold (0.5) classifies SUNG vocals over
    # music as non-speech and drops song lyrics. Lowered here so singing survives;
    # set asr_vad_filter=False entirely for music-heavy corpora.
    asr_vad_filter: bool = True
    asr_vad_threshold: float = 0.2
    huggingface_token: str | None = None

    # Vision (P3)
    yolo_model: str = "yolo11n.pt"            # YOLOv11 — fast closed-set detector (on-demand)
    yolo_device: str = "cuda"                 # set 'cpu' during heavy VLM ingest to free VRAM
    face_match_threshold: float = 0.35        # cosine sim to link a face to an existing person
    object_min_conf: float = 0.35
    # Generate a person's face avatar DURING ingest (so the People list shows faces
    # instantly). Off by default = avatars are cropped lazily on first view in the UI.
    # Only fires once per NEW person, so the added ingest cost is small.
    gen_face_thumbs: bool = False
    # Scene/activity caption backend: "" (disabled) | "ollama"
    caption_backend: str = ""
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_vlm_model: str = "moondream"       # small VLM for captions if caption_backend=ollama
    # Video sampling. Faces/objects (cheap) run on EVERY shot; the expensive ~30-45s/shot VLM
    # caption is sampled to roughly one every `video_caption_sec_per` seconds, between a floor
    # and a ceiling — so a 5-min video stays ~25-30 captions (~25-30 min) instead of one-per-shot
    # (1-2 h). Short clips with few shots are captioned in full (floor covers them).
    video_max_shots: int = 120                # cap per-asset shots for faces/objects (cheap)
    video_keyframe_per_shot: int = 1
    video_caption_sec_per: int = 11           # aim for ~1 VLM caption per this many seconds
    video_min_captions: int = 6               # ...but never fewer than this (short-clip coverage)
    video_max_captions: int = 30              # ...and never more than this (bounds long-video cost)
    # VLM input cap for VIDEO keyframes. Vision tokens scale with size — measured on this box:
    # 1024px≈69s, 768≈49s, 512≈30s per describe, tags (garment colours/objects) intact at 512.
    # Images keep 1024 (small-text OCR needs it). ≈2× faster video tagging.
    vlm_video_max_side: int = 512
    # Two-tier scanned-PDF OCR: PaddleOCR first (CPU, ~3-7s/page, no GPU contention; measured
    # BETTER recall than the VLM on real corpus pages — sheet music, signage), VLM fallback only
    # when Paddle yields almost nothing. Paddle lives in its OWN venv (numpy<2 pin) → subprocess.
    ocr_paddle_first: bool = True
    paddle_python: str = r"E:\dam-platform\.paddle-venv\Scripts\python.exe"
    paddle_script: str = r"E:\dam-platform\scripts\paddle_page_ocr.py"
    ocr_min_chars: int = 25       # Paddle result shorter than this → try the VLM for that page

    worker_port: int = 8100

    # dims (contract with API)
    dim_text: int = 1024
    dim_image: int = 768          # ViT-L-14
    dim_face: int = 512


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()

# Qdrant collection + OpenSearch index names (mirror of API constants).
QDRANT_TEXT = "dam_text"
QDRANT_IMAGE = "dam_image"
QDRANT_FACE = "dam_face"
OS_ASSETS = "dam-assets"
OS_TRANSCRIPTS = "dam-transcripts"
