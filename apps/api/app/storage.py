"""Object storage (MinIO / S3-compatible) helpers.

Stores raw binaries and derived proxies. References (s3://bucket/key) are kept in
Postgres; binaries never live in the relational store (TSA §8).
"""
import boto3
from botocore.client import Config

from .config import settings

_session = boto3.session.Session()
_client_cached = None
_public_client_cached = None


def _client():
    # Cached: a new client per call opens a new connection pool — leaked sockets under the
    # per-asset watermark/exists checks. boto3 clients are thread-safe for these calls.
    global _client_cached
    if _client_cached is None:
        _client_cached = _session.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
            use_ssl=settings.s3_secure,
        )
    return _client_cached


def _public_client():
    global _public_client_cached
    if _public_client_cached is None:
        _public_client_cached = _session.client(
            "s3",
            endpoint_url=settings.s3_public_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
            use_ssl=settings.s3_secure,
        )
    return _public_client_cached


def ensure_bucket() -> None:
    c = _client()
    existing = {b["Name"] for b in c.list_buckets().get("Buckets", [])}
    if settings.s3_bucket not in existing:
        c.create_bucket(Bucket=settings.s3_bucket)


def put_object(key: str, data: bytes, content_type: str | None = None) -> str:
    c = _client()
    extra = {"ContentType": content_type} if content_type else {}
    c.put_object(Bucket=settings.s3_bucket, Key=key, Body=data, **extra)
    return f"s3://{settings.s3_bucket}/{key}"


def get_bytes(key: str) -> bytes:
    """Fetch a stored object's raw bytes (used to render watermarked previews)."""
    return _client().get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()


def object_exists(key: str) -> bool:
    """True if the object is already stored (used to skip regenerating cached crops)."""
    try:
        _client().head_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except Exception:
        return False


def presigned_get(key: str, expires: int = 3600) -> str:
    """Browser-reachable URL for a stored object (uses the public endpoint)."""
    return _public_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires,
    )


def uri_to_key(storage_uri: str) -> str:
    """s3://bucket/path/to/key -> path/to/key"""
    prefix = f"s3://{settings.s3_bucket}/"
    return storage_uri[len(prefix):] if storage_uri.startswith(prefix) else storage_uri
