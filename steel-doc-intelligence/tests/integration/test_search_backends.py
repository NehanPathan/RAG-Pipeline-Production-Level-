"""What only a real backend can tell us.

Every assertion here failed to be catchable by a mock: filter *shape*,
mapping *type*, and the interaction between the two backends that hybrid
retrieval depends on.
"""

from __future__ import annotations

import uuid

import pytest

from src.domain.entities.document import ChunkMetadata, ChunkType, DocumentChunk
from src.domain.repositories.search_repository import BM25SearchFilter
from src.domain.repositories.vector_repository import VectorSearchFilter
from src.domain.value_objects.sensitivity import Sensitivity

pytestmark = pytest.mark.integration

ALICE = uuid.UUID("00000000-0000-0000-0000-00000000a11c")
BOB = uuid.UUID("00000000-0000-0000-0000-00000000b0b0")
PROJECT = uuid.UUID("00000000-0000-0000-0000-0000000004a1")


def _chunk(
    *,
    content: str = "Beam B-14 is an ISMB 300 in Fe 415.",
    user_id: uuid.UUID = ALICE,
    project_id: uuid.UUID | None = None,
    is_latest: bool = True,
    sensitivity: Sensitivity = Sensitivity.INTERNAL,
    embedding: list[float] | None = None,
    entities: list[dict] | None = None,
) -> DocumentChunk:
    return DocumentChunk(
        document_id=uuid.uuid4(),
        content=content,
        position=0,
        chunk_type=ChunkType.CHILD,
        embedding=embedding or [0.1] * 8,
        embedding_model="test",
        chunk_metadata=ChunkMetadata(
            page_number=3,
            section_title="4.2 Primary Framing",
            heading_level=2,
            entities=entities or [],
        ),
        user_id=user_id,
        project_id=project_id,
        drawing_number="S-104",
        revision_label="C",
        is_latest=is_latest,
        domain="Engineering",
        tags=["ismb-300"],
        file_type="pdf",
        document_name="S-104 Rev C.pdf",
        sensitivity=sensitivity,
    )


class TestPayloadRoundTrip:
    """The serializer is shared, but only a real store proves the payload it
    produces is actually storable and readable back."""

    async def test_qdrant_preserves_every_field(self, qdrant_repo):
        original = _chunk(entities=[{"canonical": "ISMB 300"}])
        await qdrant_repo.upsert_batch([original])

        results = await qdrant_repo.search([0.1] * 8, top_k=5)

        assert results, "the chunk should be retrievable"
        chunk = results[0].chunk
        assert chunk.id == original.id
        assert chunk.chunk_metadata.section_title == "4.2 Primary Framing"
        assert chunk.chunk_metadata.heading_level == 2
        assert chunk.user_id == ALICE
        assert chunk.drawing_number == "S-104"
        assert chunk.revision_label == "C"
        assert chunk.sensitivity is Sensitivity.INTERNAL

    async def test_elasticsearch_preserves_every_field(self, es_repo):
        original = _chunk()
        await es_repo.index_batch([original])

        results = await es_repo.search("ISMB", top_k=5)

        assert results
        chunk = results[0].chunk
        assert chunk.chunk_metadata.section_title == "4.2 Primary Framing"
        assert chunk.user_id == ALICE
        assert chunk.is_latest is True


class TestAccessScope:
    """Reachability is owner OR project member. A mock cannot tell us whether
    the filter narrows as a unit -- and getting it wrong either hides a
    caller's own documents or exposes a project they are not in."""

    async def test_qdrant_owner_sees_own_document(self, qdrant_repo):
        await qdrant_repo.upsert_batch([_chunk(user_id=ALICE)])

        results = await qdrant_repo.search(
            [0.1] * 8, top_k=5, filters=VectorSearchFilter(user_id=ALICE)
        )

        assert len(results) == 1

    async def test_qdrant_stranger_sees_nothing(self, qdrant_repo):
        await qdrant_repo.upsert_batch([_chunk(user_id=ALICE)])

        results = await qdrant_repo.search(
            [0.1] * 8, top_k=5, filters=VectorSearchFilter(user_id=BOB)
        )

        assert results == []

    async def test_qdrant_project_member_sees_a_colleagues_document(self, qdrant_repo):
        """The whole point of projects: two engineers on the same job see each
        other's drawings."""
        await qdrant_repo.upsert_batch([_chunk(user_id=ALICE, project_id=PROJECT)])

        results = await qdrant_repo.search(
            [0.1] * 8,
            top_k=5,
            filters=VectorSearchFilter(user_id=BOB, project_ids=[PROJECT]),
        )

        assert len(results) == 1

    async def test_qdrant_owner_keeps_documents_outside_their_projects(self, qdrant_repo):
        """The disjunction, tested from the other side. Expressed as two AND
        conditions instead, this returns nothing."""
        await qdrant_repo.upsert_batch([_chunk(user_id=ALICE, project_id=None)])

        results = await qdrant_repo.search(
            [0.1] * 8,
            top_k=5,
            filters=VectorSearchFilter(user_id=ALICE, project_ids=[PROJECT]),
        )

        assert len(results) == 1, "a personal document must survive a project scope"

    async def test_elasticsearch_agrees_with_qdrant(self, es_repo):
        """If the two backends disagree, hybrid retrieval leaks through
        whichever is looser."""
        await es_repo.index_batch(
            [_chunk(user_id=ALICE, project_id=None), _chunk(user_id=BOB, project_id=PROJECT)]
        )

        mine = await es_repo.search(
            "ISMB", top_k=10, filters=BM25SearchFilter(user_id=ALICE, project_ids=[PROJECT])
        )

        owners = {c.chunk.user_id for c in mine}
        assert owners == {ALICE, BOB}, "own document plus the project one, and nothing else"

    async def test_elasticsearch_excludes_unreachable_documents(self, es_repo):
        await es_repo.index_batch([_chunk(user_id=ALICE, project_id=None)])

        results = await es_repo.search("ISMB", top_k=10, filters=BM25SearchFilter(user_id=BOB))

        assert results == []


class TestRevisionFiltering:
    async def test_qdrant_latest_only_hides_superseded_sheets(self, qdrant_repo):
        await qdrant_repo.upsert_batch(
            [_chunk(content="Rev C current", is_latest=True),
             _chunk(content="Rev B superseded", is_latest=False)]
        )

        results = await qdrant_repo.search(
            [0.1] * 8, top_k=10, filters=VectorSearchFilter(latest_only=True)
        )

        assert len(results) == 1
        assert "current" in results[0].chunk.content

    async def test_elasticsearch_latest_only_hides_superseded_sheets(self, es_repo):
        await es_repo.index_batch(
            [_chunk(content="ISMB current sheet", is_latest=True),
             _chunk(content="ISMB superseded sheet", is_latest=False)]
        )

        results = await es_repo.search(
            "ISMB", top_k=10, filters=BM25SearchFilter(latest_only=True)
        )

        assert len(results) == 1
        assert "current" in results[0].chunk.content

    async def test_history_is_available_when_asked_for(self, qdrant_repo):
        await qdrant_repo.upsert_batch(
            [_chunk(is_latest=True), _chunk(is_latest=False)]
        )

        results = await qdrant_repo.search([0.1] * 8, top_k=10)

        assert len(results) == 2


class TestClearanceFiltering:
    async def test_over_classified_chunks_never_enter_the_candidate_set(self, qdrant_repo):
        await qdrant_repo.upsert_batch(
            [_chunk(sensitivity=Sensitivity.PUBLIC),
             _chunk(sensitivity=Sensitivity.RESTRICTED)]
        )

        results = await qdrant_repo.search(
            [0.1] * 8,
            top_k=10,
            filters=VectorSearchFilter(
                sensitivity_in=Sensitivity.values_at_or_below(Sensitivity.PUBLIC)
            ),
        )

        assert [c.chunk.sensitivity for c in results] == [Sensitivity.PUBLIC]

    async def test_clearance_and_access_scope_both_apply(self, qdrant_repo):
        """Project membership grants reach, clearance grants depth. A member
        still cannot read above their clearance."""
        await qdrant_repo.upsert_batch(
            [_chunk(user_id=ALICE, project_id=PROJECT, sensitivity=Sensitivity.RESTRICTED)]
        )

        results = await qdrant_repo.search(
            [0.1] * 8,
            top_k=10,
            filters=VectorSearchFilter(
                user_id=BOB,
                project_ids=[PROJECT],
                sensitivity_in=Sensitivity.values_at_or_below(Sensitivity.INTERNAL),
            ),
        )

        assert results == []


class TestPartialUpdates:
    """The P0 reclassification bug, from both directions.

    The upsert path silently no-opped because chunks reloaded from Postgres
    carry no embedding, and the Elasticsearch re-index blanked the
    denormalized tenant fields. Only a real store shows either.
    """

    async def test_qdrant_payload_update_reaches_the_store(self, qdrant_repo):
        chunk = _chunk(sensitivity=Sensitivity.INTERNAL)
        await qdrant_repo.upsert_batch([chunk])

        await qdrant_repo.set_payload_by_document(
            chunk.document_id, {"sensitivity": Sensitivity.RESTRICTED.value}
        )

        results = await qdrant_repo.search([0.1] * 8, top_k=5)
        assert results[0].chunk.sensitivity is Sensitivity.RESTRICTED

    async def test_qdrant_update_preserves_the_tenant_field(self, qdrant_repo):
        """The regression: after a reclassification the document must still be
        reachable by its owner."""
        chunk = _chunk(user_id=ALICE)
        await qdrant_repo.upsert_batch([chunk])

        await qdrant_repo.set_payload_by_document(
            chunk.document_id, {"sensitivity": Sensitivity.CONFIDENTIAL.value}
        )

        results = await qdrant_repo.search(
            [0.1] * 8, top_k=5, filters=VectorSearchFilter(user_id=ALICE)
        )
        assert len(results) == 1

    async def test_elasticsearch_update_preserves_the_tenant_field(self, es_repo):
        """This is the exact failure: a full re-index wrote user_id as null and
        the document vanished from its own owner's keyword search."""
        chunk = _chunk(user_id=ALICE)
        await es_repo.index_batch([chunk])

        updated = await es_repo.update_fields_by_document(
            chunk.document_id, {"sensitivity": Sensitivity.CONFIDENTIAL.value}
        )

        assert updated == 1
        results = await es_repo.search(
            "ISMB", top_k=5, filters=BM25SearchFilter(user_id=ALICE)
        )
        assert len(results) == 1
        assert results[0].chunk.sensitivity is Sensitivity.CONFIDENTIAL


class TestEntityIndex:
    async def test_canonical_designations_are_exactly_matchable(self, es_repo):
        """The steel index rides on an existing keyword field. If the mapping
        analysed it instead, "ISMB 300" would match "ISMB 400" and nobody
        would notice until an engineer did."""
        await es_repo.index_batch(
            [_chunk(content="beam one", entities=[{"canonical": "ISMB 300"}]),
             _chunk(content="beam two", entities=[{"canonical": "ISMB 400"}])]
        )

        response = await es_repo._client.search(
            index=es_repo._index_name,
            body={"query": {"term": {"entity_canonicals": "ISMB 300"}}},
        )

        assert response["hits"]["total"]["value"] == 1
