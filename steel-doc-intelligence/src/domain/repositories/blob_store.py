"""Port for durable storage of original files and derived artifacts.

Uploads previously went to a local `./uploads` directory that docker-compose
did not mount as a volume, so every container restart destroyed the
originals -- while the database still listed the documents as indexed. That
is not a scaling problem, it is data loss with no error attached.

The port is deliberately stream-oriented. The upload route used to read the
whole file into memory *before* checking its size, so `max_file_size_bytes`
bounded what was accepted but not what a request could allocate; a 2 GB
upload was a 2 GB allocation regardless of the limit.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass


@dataclass(frozen=True)
class StoredBlob:
    key: str
    size_bytes: int
    content_type: str = "application/octet-stream"
    checksum_sha256: str = ""


class BlobTooLargeError(ValueError):
    """The stream exceeded the configured limit.

    Raised mid-stream rather than after buffering, so the size limit bounds
    memory as well as what is accepted.
    """


class BlobStore(ABC):
    @abstractmethod
    async def put_stream(
        self,
        key: str,
        stream: AsyncIterator[bytes],
        content_type: str = "application/octet-stream",
        max_bytes: int | None = None,
    ) -> StoredBlob:
        """Store a stream, aborting if it exceeds `max_bytes`."""

    @abstractmethod
    def get_stream(self, key: str) -> AsyncIterator[bytes]:
        """Read a blob back in chunks.

        Declared as a plain `def` returning an `AsyncIterator` -- not `async
        def` -- so implementations are async generators and callers write
        `async for chunk in store.get_stream(key)` with no await on the call
        itself. Same convention as `LLMProvider.stream`.
        """

    @abstractmethod
    async def get_bytes(self, key: str) -> bytes:
        """Read a whole blob. Only for artifacts known to be small."""

    @abstractmethod
    async def delete(self, key: str) -> bool: ...

    @abstractmethod
    async def exists(self, key: str) -> bool: ...

    @abstractmethod
    async def ensure_ready(self) -> None:
        """Create the bucket or directory if absent. Safe to call repeatedly."""


def document_key(document_id: str, file_name: str) -> str:
    """Key for an uploaded original.

    Prefixed by document id rather than by date or user: deletion and
    retention both operate per document, and a prefix that matches the unit
    of deletion makes those a single ranged operation instead of a scan.
    """
    return f"documents/{document_id}/original/{file_name}"


def derived_key(document_id: str, kind: str, name: str) -> str:
    """Key for something generated from a document -- a page render, a
    thumbnail, a DXF converted from DWG.

    Under the same document prefix so deleting a document removes what was
    derived from it too. A derived artifact that outlives its source is a
    quiet way to keep serving content that was supposed to be gone.
    """
    return f"documents/{document_id}/derived/{kind}/{name}"
