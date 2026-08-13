"""Filesystem blob store.

The default, because it needs nothing running: `make dev` works, tests work,
and a single-node deployment with a mounted volume is a legitimate
production choice for a small practice.

It is a real implementation of the port, not a stub -- same streaming
semantics, same size enforcement, same key scheme -- so moving to S3 is a
configuration change rather than a behavioural one.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import AsyncIterator
from pathlib import Path

import aiofiles

from src.domain.repositories.blob_store import BlobStore, BlobTooLargeError, StoredBlob
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

_CHUNK = 1024 * 1024


class LocalBlobStore(BlobStore):
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)

    def _path(self, key: str) -> Path:
        # Keys are built by this application, never taken from a request, but
        # resolving and re-checking costs nothing and turns a future mistake
        # into an error rather than a path traversal.
        path = (self._root / key).resolve()
        root = self._root.resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Blob key escapes the storage root: {key!r}")
        return path

    async def ensure_ready(self) -> None:
        self._root.mkdir(parents=True, exist_ok=True)

    async def put_stream(
        self,
        key: str,
        stream: AsyncIterator[bytes],
        content_type: str = "application/octet-stream",
        max_bytes: int | None = None,
    ) -> StoredBlob:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256()
        size = 0
        # Written to a temporary name and moved into place, so an aborted
        # upload cannot leave a truncated file that looks complete.
        staging = path.with_suffix(path.suffix + ".partial")
        try:
            async with aiofiles.open(staging, "wb") as handle:
                async for chunk in stream:
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise BlobTooLargeError(
                            f"Upload exceeds the {max_bytes} byte limit."
                        )
                    digest.update(chunk)
                    await handle.write(chunk)
        except BaseException:
            staging.unlink(missing_ok=True)
            raise

        staging.replace(path)
        logger.info("blob_stored", key=key, size=size, backend="local")
        return StoredBlob(
            key=key, size_bytes=size, content_type=content_type, checksum_sha256=digest.hexdigest()
        )

    async def get_stream(self, key: str) -> AsyncIterator[bytes]:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        async with aiofiles.open(path, "rb") as handle:
            while chunk := await handle.read(_CHUNK):
                yield chunk

    async def get_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not path.exists():
            raise FileNotFoundError(key)
        async with aiofiles.open(path, "rb") as handle:
            return bytes(await handle.read())

    async def delete(self, key: str) -> bool:
        path = self._path(key)
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            return True
        if not path.exists():
            return False
        path.unlink()
        return True

    async def exists(self, key: str) -> bool:
        return self._path(key).exists()
