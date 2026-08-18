"""S3-compatible blob store.

One implementation covers MinIO (dev, on-prem, air-gapped) and real S3,
because they speak the same API. That matters here beyond convenience: a
structural practice may be contractually unable to let drawings leave its
network, and "run MinIO instead" has to be a config change rather than a
port.

Uploads use multipart above a threshold so a large drawing set never has to
be buffered whole. Below it, a single PUT avoids the three round-trips
multipart costs.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Any

from src.domain.repositories.blob_store import BlobStore, BlobTooLargeError, StoredBlob
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

_CHUNK = 1024 * 1024
# S3 requires every part except the last to be at least 5 MiB.
MIN_PART_BYTES = 5 * 1024 * 1024


class S3BlobStore(BlobStore):
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key_id: str = "",
        secret_access_key: str = "",
        use_path_style: bool = True,
        multipart_threshold_bytes: int = 8 * 1024 * 1024,
        session_factory: Any = None,
    ) -> None:
        self._bucket = bucket
        self._endpoint_url = endpoint_url or None
        self._region = region
        self._access_key_id = access_key_id or None
        self._secret_access_key = secret_access_key or None
        # MinIO addresses buckets by path, not by virtual host, and a
        # hostname-style request to it 404s in a way that reads like a
        # missing bucket.
        self._use_path_style = use_path_style
        self._multipart_threshold = max(multipart_threshold_bytes, MIN_PART_BYTES)
        self._session_factory = session_factory

    def _client(self) -> Any:
        if self._session_factory is not None:
            return self._session_factory()

        import aioboto3
        from botocore.config import Config

        session = aioboto3.Session()
        return session.client(
            "s3",
            endpoint_url=self._endpoint_url,
            region_name=self._region,
            aws_access_key_id=self._access_key_id,
            aws_secret_access_key=self._secret_access_key,
            config=Config(s3={"addressing_style": "path" if self._use_path_style else "auto"}),
        )

    async def ensure_ready(self) -> None:
        async with self._client() as client:
            try:
                await client.head_bucket(Bucket=self._bucket)
                return
            except Exception:
                pass
            try:
                await client.create_bucket(Bucket=self._bucket)
                logger.info("blob_bucket_created", bucket=self._bucket)
            except Exception as exc:
                # A concurrent worker winning the race, or a bucket the
                # deployment is expected to have provisioned already. Neither
                # should stop startup; a genuinely missing bucket surfaces on
                # the first upload with a clearer message than this would.
                logger.warning(
                    "blob_bucket_not_created", bucket=self._bucket, error=str(exc)
                )

    async def put_stream(
        self,
        key: str,
        stream: AsyncIterator[bytes],
        content_type: str = "application/octet-stream",
        max_bytes: int | None = None,
    ) -> StoredBlob:
        digest = hashlib.sha256()
        buffer = bytearray()
        size = 0

        async with self._client() as client:
            upload_id: str | None = None
            parts: list[dict[str, Any]] = []
            try:
                async for chunk in stream:
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise BlobTooLargeError(f"Upload exceeds the {max_bytes} byte limit.")
                    digest.update(chunk)
                    buffer.extend(chunk)

                    if len(buffer) >= self._multipart_threshold:
                        if upload_id is None:
                            created = await client.create_multipart_upload(
                                Bucket=self._bucket, Key=key, ContentType=content_type
                            )
                            upload_id = created["UploadId"]
                        parts.append(
                            await self._upload_part(client, key, upload_id, len(parts) + 1, bytes(buffer))
                        )
                        buffer.clear()

                if upload_id is None:
                    await client.put_object(
                        Bucket=self._bucket,
                        Key=key,
                        Body=bytes(buffer),
                        ContentType=content_type,
                    )
                else:
                    if buffer:
                        parts.append(
                            await self._upload_part(
                                client, key, upload_id, len(parts) + 1, bytes(buffer)
                            )
                        )
                    await client.complete_multipart_upload(
                        Bucket=self._bucket,
                        Key=key,
                        UploadId=upload_id,
                        MultipartUpload={"Parts": parts},
                    )
            except BaseException:
                # An abandoned multipart upload is billable storage that no
                # listing shows, so it is aborted explicitly rather than left
                # to a lifecycle rule the deployment may not have.
                if upload_id is not None:
                    try:
                        await client.abort_multipart_upload(
                            Bucket=self._bucket, Key=key, UploadId=upload_id
                        )
                    except Exception as exc:
                        logger.warning("blob_multipart_abort_failed", key=key, error=str(exc))
                raise

        logger.info("blob_stored", key=key, size=size, backend="s3", multipart=bool(parts))
        return StoredBlob(
            key=key, size_bytes=size, content_type=content_type, checksum_sha256=digest.hexdigest()
        )

    async def _upload_part(
        self, client: Any, key: str, upload_id: str, number: int, body: bytes
    ) -> dict[str, Any]:
        response = await client.upload_part(
            Bucket=self._bucket, Key=key, UploadId=upload_id, PartNumber=number, Body=body
        )
        return {"ETag": response["ETag"], "PartNumber": number}

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        async with self._client() as client:
            response = await client.get_object(Bucket=self._bucket, Key=key)
            async for chunk in response["Body"].iter_chunks(_CHUNK):
                yield chunk

    async def get_bytes(self, key: str) -> bytes:
        async with self._client() as client:
            response = await client.get_object(Bucket=self._bucket, Key=key)
            return bytes(await response["Body"].read())

    async def delete(self, key: str) -> bool:
        async with self._client() as client:
            try:
                await client.delete_object(Bucket=self._bucket, Key=key)
                return True
            except Exception as exc:
                logger.warning("blob_delete_failed", key=key, error=str(exc))
                return False

    async def exists(self, key: str) -> bool:
        async with self._client() as client:
            try:
                await client.head_object(Bucket=self._bucket, Key=key)
                return True
            except Exception:
                return False
