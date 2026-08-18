import uuid

import pytest

from src.application.use_cases.register_revision import RegisterRevision
from src.domain.entities.document import Document
from src.domain.entities.project import Drawing


class _FakeDrawings:
    def __init__(self) -> None:
        self.drawings: dict[tuple, Drawing] = {}
        self.current: dict[uuid.UUID, uuid.UUID] = {}
        self.revisions: dict[uuid.UUID, list[uuid.UUID]] = {}

    async def get_or_create(
        self, drawing_number, project_id=None, sheet_number=None, discipline=None, title=None
    ) -> Drawing:
        key = (drawing_number, project_id, sheet_number)
        if key not in self.drawings:
            self.drawings[key] = Drawing(
                drawing_number=drawing_number,
                project_id=project_id,
                sheet_number=sheet_number,
                title=title,
            )
        return self.drawings[key]

    async def get_by_id(self, drawing_id):
        return next((d for d in self.drawings.values() if d.id == drawing_id), None)

    async def current_revision_id(self, drawing_id):
        return self.current.get(drawing_id)

    async def revision_document_ids(self, drawing_id):
        return self.revisions.get(drawing_id, [])


class _FakeDocuments:
    def __init__(self) -> None:
        self.store: dict[uuid.UUID, Document] = {}
        self.updates: list[uuid.UUID] = []

    async def get_by_id(self, document_id):
        return self.store.get(document_id)

    async def update(self, document: Document) -> Document:
        self.store[document.id] = document
        self.updates.append(document.id)
        return document


class _FakeStore:
    def __init__(self) -> None:
        self.payloads: list[tuple[uuid.UUID, dict]] = []

    async def set_payload_by_document(self, document_id, payload):
        self.payloads.append((document_id, payload))

    async def update_fields_by_document(self, document_id, fields):
        self.payloads.append((document_id, fields))
        return 1


def _document(user_id=None) -> Document:
    return Document(
        file_name="S-104.pdf",
        file_type="pdf",
        file_size_bytes=1,
        user_id=user_id or uuid.uuid4(),
    )


@pytest.fixture
def harness():
    drawings, documents = _FakeDrawings(), _FakeDocuments()
    vector, search = _FakeStore(), _FakeStore()
    invalidated: list[uuid.UUID] = []

    async def _invalidate(document_id: uuid.UUID) -> None:
        invalidated.append(document_id)

    use_case = RegisterRevision(
        drawing_repo=drawings,  # type: ignore[arg-type]
        document_repo=documents,  # type: ignore[arg-type]
        vector_repo=vector,  # type: ignore[arg-type]
        search_repo=search,  # type: ignore[arg-type]
        cache_invalidator=_invalidate,
    )
    return use_case, drawings, documents, vector, search, invalidated


class TestFirstRevision:
    async def test_becomes_current(self, harness):
        use_case, *_ = harness

        result = await use_case.execute(_document(), drawing_number="S-104", revision_label="A")

        assert result.is_current
        assert result.superseded_document_id is None

    async def test_is_attached_to_a_drawing(self, harness):
        use_case, _drawings, *_ = harness

        result = await use_case.execute(_document(), drawing_number="S-104", revision_label="A")

        assert result.drawing.drawing_number == "S-104"
        assert result.document.drawing_id == result.drawing.id


class TestSupersession:
    async def test_a_newer_revision_supersedes_the_current_one(self, harness):
        use_case, drawings, documents, *_ = harness
        rev_a = _document()
        await use_case.execute(rev_a, drawing_number="S-104", revision_label="A")
        drawings.current[rev_a.drawing_id] = rev_a.id
        documents.store[rev_a.id] = rev_a

        rev_b = _document()
        result = await use_case.execute(rev_b, drawing_number="S-104", revision_label="B")

        assert result.is_current
        assert result.superseded_document_id == rev_a.id
        assert rev_a.is_latest is False

    async def test_the_superseded_document_records_its_replacement(self, harness):
        """"Superseded by Rev B" is a usable dead end; "superseded" is not."""
        use_case, drawings, documents, *_ = harness
        rev_a = _document()
        await use_case.execute(rev_a, drawing_number="S-104", revision_label="A")
        drawings.current[rev_a.drawing_id] = rev_a.id
        documents.store[rev_a.id] = rev_a

        rev_b = _document()
        await use_case.execute(rev_b, drawing_number="S-104", revision_label="B")

        assert rev_a.superseded_by_document_id == rev_b.id
        assert rev_a.superseded_at is not None

    async def test_an_out_of_order_upload_does_not_demote_the_current_sheet(self, harness):
        """Drawing sets arrive out of sequence. Treating "most recently
        uploaded" as "current" would make the register wrong in exactly the
        case a register exists to prevent."""
        use_case, drawings, documents, *_ = harness
        rev_c = _document()
        await use_case.execute(rev_c, drawing_number="S-104", revision_label="C")
        drawings.current[rev_c.drawing_id] = rev_c.id
        documents.store[rev_c.id] = rev_c

        rev_b = _document()
        result = await use_case.execute(rev_b, drawing_number="S-104", revision_label="B")

        assert result.is_current is False
        assert rev_c.is_latest is True
        assert rev_b.superseded_by_document_id == rev_c.id


class TestPropagation:
    async def test_both_search_backends_learn_the_flag(self, harness):
        """Retrieval filters on `is_latest` inside Qdrant and Elasticsearch,
        before any candidate reaches the application. A superseded document
        whose chunks still say `is_latest` is a wrong answer delivered
        confidently."""
        use_case, drawings, documents, vector, search, _ = harness
        rev_a = _document()
        await use_case.execute(rev_a, drawing_number="S-104", revision_label="A")
        drawings.current[rev_a.drawing_id] = rev_a.id
        documents.store[rev_a.id] = rev_a
        vector.payloads.clear()
        search.payloads.clear()

        rev_b = _document()
        await use_case.execute(rev_b, drawing_number="S-104", revision_label="B")

        assert (rev_a.id, {"is_latest": False}) in vector.payloads
        assert (rev_a.id, {"is_latest": False}) in search.payloads
        assert (rev_b.id, {"is_latest": True}) in vector.payloads
        assert (rev_b.id, {"is_latest": True}) in search.payloads


class TestCacheInvalidation:
    async def test_the_whole_revision_family_is_evicted(self, harness):
        """A cached answer built from Rev A is just as wrong once Rev C lands
        as one built from Rev B, so evicting only the superseded document
        leaves the older ones in place."""
        use_case, drawings, documents, _, _, invalidated = harness
        rev_a, rev_b = _document(), _document()
        await use_case.execute(rev_a, drawing_number="S-104", revision_label="A")
        drawing_id = rev_a.drawing_id
        drawings.current[drawing_id] = rev_a.id
        documents.store[rev_a.id] = rev_a
        drawings.revisions[drawing_id] = [rev_a.id, rev_b.id]
        invalidated.clear()

        await use_case.execute(rev_b, drawing_number="S-104", revision_label="B")

        assert set(invalidated) == {rev_a.id, rev_b.id}
