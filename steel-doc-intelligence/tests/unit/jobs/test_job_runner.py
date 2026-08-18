import uuid
from pathlib import Path

import pytest

from src.domain.entities.document import Document
from src.jobs.models import Job, JobStatus, JobType
from src.jobs.queue import InlineJobQueue
from src.jobs.runner import JobRunner


class _FakeJobRepo:
    def __init__(self) -> None:
        self.saved: list[tuple[JobStatus, int]] = []
        self.jobs: dict[uuid.UUID, Job] = {}

    async def save(self, job: Job) -> Job:
        self.saved.append((job.status, job.attempts))
        self.jobs[job.id] = job
        return job


class _FakeDocumentRepo:
    def __init__(self, document: Document | None) -> None:
        self._document = document

    async def get_by_id(self, document_id):
        return self._document


class _FakeStore:
    def __init__(self, name: str, deleted: list[str]) -> None:
        self._name = name
        self._deleted = deleted

    async def delete_by_document(self, document_id):
        self._deleted.append(self._name)
        return 1


class _FakePipeline:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.ingested: list[uuid.UUID] = []

    async def ingest(self, document, file_path):
        if self.fail:
            raise RuntimeError("docling exploded")
        self.ingested.append(document.id)


def _document() -> Document:
    return Document(
        file_name="S-104.dxf",
        file_type="dxf",
        file_size_bytes=10,
        user_id=uuid.uuid4(),
        file_path="documents/x/original/S-104.dxf",
    )


def _runner(document, pipeline, deleted=None, blob=None) -> tuple[JobRunner, _FakeJobRepo]:
    repo = _FakeJobRepo()
    deleted = deleted if deleted is not None else []
    runner = JobRunner(
        job_repo=repo,
        document_repo=_FakeDocumentRepo(document),
        ingestion_pipeline_factory=lambda: pipeline,
        chunk_repo=_FakeStore("postgres", deleted),
        vector_repo=_FakeStore("qdrant", deleted),
        search_repo=_FakeStore("elasticsearch", deleted),
        blob_store=blob,
    )
    return runner, repo


def _job(document_id, file_path: Path) -> Job:
    return Job(
        job_type=JobType.INGEST_DOCUMENT,
        document_id=document_id,
        payload={"file_path": str(file_path), "cleanup_file": False},
    )


class TestSuccess:
    async def test_the_pipeline_runs_and_the_job_succeeds(self, tmp_path):
        source = tmp_path / "S-104.dxf"
        source.write_bytes(b"x")
        document = _document()
        pipeline = _FakePipeline()
        runner, _repo = _runner(document, pipeline)

        job = await runner.run(_job(document.id, source))

        assert job.status is JobStatus.SUCCEEDED
        assert pipeline.ingested == [document.id]

    async def test_status_transitions_are_recorded(self, tmp_path):
        """"What happened to my upload" has to be answerable from the job
        row, which means every transition is written, not just the last."""
        source = tmp_path / "S-104.dxf"
        source.write_bytes(b"x")
        document = _document()
        runner, repo = _runner(document, _FakePipeline())

        await runner.run(_job(document.id, source))

        assert [status for status, _ in repo.saved] == [
            JobStatus.RUNNING,
            JobStatus.SUCCEEDED,
        ]


class TestIdempotency:
    async def test_previous_output_is_cleared_before_reingesting(self, tmp_path):
        """A retry that appended would double every chunk: both copies are
        retrieved, both are cited, and the duplicate reads as
        corroboration."""
        source = tmp_path / "S-104.dxf"
        source.write_bytes(b"x")
        document = _document()
        deleted: list[str] = []
        runner, _ = _runner(document, _FakePipeline(), deleted=deleted)

        await runner.run(_job(document.id, source))

        assert set(deleted) == {"postgres", "qdrant", "elasticsearch"}


class TestFailure:
    async def test_the_error_is_recorded_and_re_raised(self, tmp_path):
        """Re-raised so arq applies its backoff. Swallowing it would mark the
        job failed and then tell the worker it succeeded, which is how a
        queue quietly stops retrying."""
        source = tmp_path / "S-104.dxf"
        source.write_bytes(b"x")
        document = _document()
        runner, repo = _runner(document, _FakePipeline(fail=True))

        with pytest.raises(RuntimeError, match="docling exploded"):
            await runner.run(_job(document.id, source))

        job = next(iter(repo.jobs.values()))
        assert job.status is JobStatus.FAILED
        assert "docling exploded" in (job.error_message or "")

    async def test_a_missing_document_fails_the_job(self, tmp_path):
        runner, _ = _runner(None, _FakePipeline())

        with pytest.raises(ValueError, match="no longer exists"):
            await runner.run(_job(uuid.uuid4(), tmp_path / "gone.dxf"))

    async def test_attempts_are_counted(self, tmp_path):
        source = tmp_path / "S-104.dxf"
        source.write_bytes(b"x")
        document = _document()
        runner, _repo = _runner(document, _FakePipeline(fail=True))
        job = _job(document.id, source)

        for _ in range(3):
            with pytest.raises(RuntimeError):
                await runner.run(job)

        assert job.attempts == 3
        assert job.is_exhausted


class TestMaterialise:
    async def test_the_blob_is_fetched_when_no_local_file_exists(self, tmp_path):
        """The worker is a different process from the API, so it cannot rely
        on a file the upload request happened to leave behind."""

        class _Blob:
            async def get_stream(self, key):
                yield b"from-blob"

        document = _document()
        pipeline = _FakePipeline()
        runner, _ = _runner(document, pipeline, blob=_Blob())

        job = Job(job_type=JobType.INGEST_DOCUMENT, document_id=document.id, payload={})
        await runner.run(job)

        assert pipeline.ingested == [document.id]

    async def test_no_local_file_and_no_blob_is_an_error(self, tmp_path):
        document = _document()
        runner, _ = _runner(document, _FakePipeline(), blob=None)

        job = Job(job_type=JobType.INGEST_DOCUMENT, document_id=document.id, payload={})
        with pytest.raises(ValueError, match="no readable source"):
            await runner.run(job)


class TestInlineQueue:
    async def test_it_writes_a_job_row_like_arq_does(self, tmp_path):
        """Not a stub: development and production answer "how did this job
        end up failed" the same way."""
        repo = _FakeJobRepo()
        ran: list[Job] = []

        queue = InlineJobQueue(job_repo=repo, runner=lambda job: _record(ran, job))
        job = await queue.enqueue(JobType.INGEST_DOCUMENT, document_id=uuid.uuid4())

        assert job.id in repo.jobs
        assert ran and ran[0].id == job.id

    async def test_it_does_not_retry_in_the_request_process(self):
        repo = _FakeJobRepo()
        queue = InlineJobQueue(job_repo=repo, runner=lambda job: _record([], job))

        job = await queue.enqueue(JobType.INGEST_DOCUMENT, document_id=uuid.uuid4())

        assert job.max_attempts == 1


async def _record(sink: list, job: Job) -> None:
    sink.append(job)
