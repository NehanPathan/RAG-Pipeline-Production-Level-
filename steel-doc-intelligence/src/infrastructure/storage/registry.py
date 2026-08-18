from __future__ import annotations

from pathlib import Path

from src.config import Settings
from src.domain.repositories.blob_store import BlobStore
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


def get_blob_store(settings: Settings) -> BlobStore:
    """Select the configured blob store.

    Local is the default because it needs nothing running. S3 is the same
    code path for MinIO and for real S3 -- which is what lets a practice that
    cannot let drawings leave its network run this unchanged.
    """
    provider = (settings.blob_store_provider or "local").strip().lower()

    if provider == "s3":
        from src.infrastructure.storage.s3_blob_store import S3BlobStore

        return S3BlobStore(
            bucket=settings.blob_bucket,
            endpoint_url=settings.blob_endpoint_url or None,
            region=settings.blob_region,
            access_key_id=settings.blob_access_key_id,
            secret_access_key=settings.blob_secret_access_key,
            use_path_style=settings.blob_use_path_style,
        )

    if provider != "local":
        logger.warning("blob_store_unknown_provider", configured=provider)

    from src.infrastructure.storage.local_blob_store import LocalBlobStore

    return LocalBlobStore(Path(settings.blob_local_root))
