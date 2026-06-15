"""Centralised settings, loaded from environment / .env."""
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Dev-only credentials that MUST be overridden before a real deployment.
_INSECURE_DEFAULTS = {
    "jwt_secret": "change-me-in-prod-please",
    "bootstrap_admin_password": "admin12345",
    "s3_secret_key": "minioadmin",
}
_DEV_ENVS = {"development", "dev", "local", "test", "ci"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    environment: str = "development"
    log_level: str = "INFO"
    api_port: int = 8000

    # Postgres
    database_url: str = "postgresql+asyncpg://dam:dam_dev_pw@localhost:5432/dam"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None

    # OpenSearch
    opensearch_url: str = "http://localhost:9200"
    opensearch_user: str | None = None
    opensearch_password: str | None = None

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Object storage
    s3_endpoint: str = "http://localhost:9000"
    s3_public_endpoint: str = "http://localhost:9000"
    s3_access_key: str = "minioadmin"
    s3_secret_key: str = "minioadmin"
    s3_bucket: str = "dam-assets"
    s3_region: str = "us-east-1"
    s3_secure: bool = False

    # Auth
    jwt_secret: str = "change-me-in-prod-please"
    jwt_alg: str = "HS256"
    access_token_ttl_min: int = 720

    # Bootstrap admin (created at startup if not present)
    bootstrap_admin_email: str = "admin@dam.local"
    bootstrap_admin_password: str = "admin12345"

    # BM25 fuzzy matching. OFF by default: the search bar already SPELL-CORRECTS the query
    # against the library's own vocabulary (SymSpell, shown as "showing results for X"), so
    # BM25 only needs EXACT term matching. Fuzzy-on-top double-corrects and invents matches
    # ("police"→"pole", "police"→"olive"). Flip on only if a deployment wants extra slack.
    search_fuzzy: bool = False

    # Embedding dims — must match what the ai-worker writes into Qdrant.
    text_embed_dim: int = 1024     # BGE-M3
    image_embed_dim: int = 768     # OpenCLIP ViT-L-14

    # Model names (display only; the model server is authoritative) — read from .env.
    text_embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    image_embed_model: str = "ViT-B-16-SigLIP2-512 (open_clip)"
    asr_model: str = "large-v3"

    @model_validator(mode="after")
    def _forbid_default_secrets_in_prod(self):
        """Fail-fast: a non-dev deployment must not run on the shipped dev credentials —
        a default JWT secret lets anyone forge admin tokens. Dev keeps its defaults."""
        if self.environment.lower() in _DEV_ENVS:
            return self
        bad = [k for k, v in _INSECURE_DEFAULTS.items() if getattr(self, k) == v]
        if "dam_dev_pw" in self.database_url:
            bad.append("database_url")
        if bad:
            raise ValueError(
                f"Refusing to start in environment={self.environment!r} with default "
                f"credentials for: {', '.join(sorted(bad))}. Override them via environment "
                f"variables before deploying.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
