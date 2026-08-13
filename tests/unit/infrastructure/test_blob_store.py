import pytest

from src.config import Settings
from src.domain.repositories.blob_store import (
    BlobTooLargeError,
    derived_key,
    document_key,
)
from src.infrastructure.storage import get_blob_store
from src.infrastructure.storage.local_blob_store import LocalBlobStore


async def _stream(*chunks: bytes):
    for chunk in chunks:
        yield chunk


@pytest.fixture
async def store(tmp_path) -> LocalBlobStore:
    blob_store = LocalBlobStore(tmp_path / "blobs")
    await blob_store.ensure_ready()
    return blob_store


class TestRoundTrip:
    async def test_stored_bytes_come_back_unchanged(self, store):
        await store.put_stream("documents/1/original/S-104.pdf", _stream(b"ISMB ", b"300"))

        assert await store.get_bytes("documents/1/original/S-104.pdf") == b"ISMB 300"

    async def test_size_and_checksum_are_reported(self, store):
        stored = await store.put_stream("k", _stream(b"abc", b"de"))

        assert stored.size_bytes == 5
        assert len(stored.checksum_sha256) == 64

    async def test_reads_back_as_a_stream(self, store):
        await store.put_stream("k", _stream(b"a" * 100))

        chunks = [chunk async for chunk in store.get_stream("k")]

        assert b"".join(chunks) == b"a" * 100

    async def test_exists_and_delete(self, store):
        await store.put_stream("k", _stream(b"x"))

        assert await store.exists("k")
        assert await store.delete("k")
        assert not await store.exists("k")

    async def test_deleting_something_absent_is_not_an_error(self, store):
        assert await store.delete("never-written") is False

    async def test_reading_something_absent_raises(self, store):
        with pytest.raises(FileNotFoundError):
            await store.get_bytes("never-written")


class TestSizeLimit:
    """The limit must bound memory, not merely what is accepted. The previous
    upload path buffered the whole file and checked afterwards, so a 2 GB
    upload was a 2 GB allocation regardless of the configured maximum."""

    async def test_exceeding_the_limit_aborts(self, store):
        with pytest.raises(BlobTooLargeError):
            await store.put_stream("k", _stream(b"a" * 10, b"b" * 10), max_bytes=15)

    async def test_an_aborted_upload_leaves_nothing_behind(self, store):
        """A truncated file that looks complete is worse than no file: the
        document row would point at content that is silently partial."""
        with pytest.raises(BlobTooLargeError):
            await store.put_stream("k", _stream(b"a" * 10, b"b" * 10), max_bytes=15)

        assert not await store.exists("k")

    async def test_exactly_at_the_limit_is_allowed(self, store):
        stored = await store.put_stream("k", _stream(b"a" * 15), max_bytes=15)

        assert stored.size_bytes == 15


class TestKeyScheme:
    def test_originals_and_derivatives_share_a_document_prefix(self):
        """Deletion and retention both operate per document, so a derived
        artifact must not outlive its source -- that would keep serving
        content that was supposed to be gone."""
        original = document_key("doc-1", "S-104.pdf")
        render = derived_key("doc-1", "pages", "1.png")

        assert original.startswith("documents/doc-1/")
        assert render.startswith("documents/doc-1/")

    async def test_a_key_cannot_escape_the_storage_root(self, store):
        with pytest.raises(ValueError, match="escapes the storage root"):
            await store.put_stream("../../etc/passwd", _stream(b"x"))


class TestRegistry:
    def _settings(self, **overrides) -> Settings:
        return Settings(_env_file=None, **overrides)

    def test_local_is_the_default(self, tmp_path):
        store = get_blob_store(self._settings(blob_local_root=str(tmp_path)))

        assert isinstance(store, LocalBlobStore)

    def test_s3_can_be_selected(self):
        store = get_blob_store(self._settings(blob_store_provider="s3"))

        assert type(store).__name__ == "S3BlobStore"

    def test_an_unknown_provider_falls_back_to_local(self, tmp_path):
        store = get_blob_store(
            self._settings(blob_store_provider="dropbox", blob_local_root=str(tmp_path))
        )

        assert isinstance(store, LocalBlobStore)
