"""Facets and structured search, against a real Elasticsearch.

Aggregations are the kind of thing a mock cannot verify at all: the result
depends entirely on the mapping. If `entity_canonicals` were analysed rather
than a keyword, the buckets would be "ismb" and "300" instead of "ISMB 300",
and every filter built from them would be wrong.
"""

from __future__ import annotations

import uuid

import pytest

from src.domain.entities.document import ChunkMetadata, DocumentChunk
from src.domain.repositories.search_repository import BM25SearchFilter
from src.domain.value_objects.sensitivity import Sensitivity

pytestmark = pytest.mark.integration

ALICE = uuid.UUID("00000000-0000-0000-0000-00000000a11c")
BOB = uuid.UUID("00000000-0000-0000-0000-00000000b0b0")
PROJECT_A = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
PROJECT_B = uuid.UUID("00000000-0000-0000-0000-0000000000b1")


def _chunk(
    *,
    content: str,
    project_id: uuid.UUID | None,
    project_number: str | None,
    drawing_number: str,
    entities: list[str],
    user_id: uuid.UUID = ALICE,
    is_latest: bool = True,
) -> DocumentChunk:
    return DocumentChunk(
        document_id=uuid.uuid4(),
        content=content,
        position=0,
        embedding=[0.1] * 8,
        chunk_metadata=ChunkMetadata(
            entities=[{"canonical": e} for e in entities], page_number=1
        ),
        user_id=user_id,
        project_id=project_id,
        project_number=project_number,
        drawing_number=drawing_number,
        revision_label="C",
        is_latest=is_latest,
        domain="Engineering",
        file_type="dxf",
        tags=["structural"],
        document_name=f"{drawing_number}.dxf",
        sensitivity=Sensitivity.INTERNAL,
    )


@pytest.fixture
async def corpus(es_repo):
    await es_repo.index_batch(
        [
            _chunk(
                content="Beam B-14 is ISMB 300",
                project_id=PROJECT_A,
                project_number="2024-0117",
                drawing_number="S-104",
                entities=["ISMB 300", "Fe 415"],
            ),
            _chunk(
                content="Beam B-20 is ISMB 400",
                project_id=PROJECT_A,
                project_number="2024-0117",
                drawing_number="S-105",
                entities=["ISMB 400"],
            ),
            _chunk(
                content="Column C-01 is ISMC 200",
                project_id=PROJECT_B,
                project_number="2025-0042",
                drawing_number="S-201",
                entities=["ISMC 200"],
                user_id=BOB,
            ),
        ]
    )
    return es_repo


class TestFacets:
    async def test_designations_aggregate_as_whole_tokens(self, corpus):
        """The mapping test that matters. Analysed, "ISMB 300" would bucket
        as "ismb" and "300", and every filter built from the facet list
        would be wrong."""
        facets = await corpus.aggregate_facets(["entity_canonicals"])

        values = {value for value, _ in facets["entity_canonicals"]}
        assert "ISMB 300" in values
        assert "ismb" not in values

    async def test_counts_are_real(self, corpus):
        facets = await corpus.aggregate_facets(["project_number"])

        counts = dict(facets["project_number"])
        assert counts["2024-0117"] == 2
        assert counts["2025-0042"] == 1

    async def test_several_fields_in_one_round_trip(self, corpus):
        facets = await corpus.aggregate_facets(
            ["project_number", "drawing_number", "file_type"]
        )

        assert set(facets) == {"project_number", "drawing_number", "file_type"}
        assert len(facets["drawing_number"]) == 3

    async def test_facets_respect_the_access_scope(self, corpus):
        """A facet list must never reveal that a project exists which the
        caller cannot read."""
        facets = await corpus.aggregate_facets(
            ["project_number"], filters=BM25SearchFilter(user_id=BOB)
        )

        assert {v for v, _ in facets["project_number"]} == {"2025-0042"}

    async def test_superseded_revisions_are_excluded_by_default(self, es_repo):
        await es_repo.index_batch(
            [
                _chunk(
                    content="current",
                    project_id=PROJECT_A,
                    project_number="P-CURRENT",
                    drawing_number="S-1",
                    entities=[],
                ),
                _chunk(
                    content="old",
                    project_id=PROJECT_A,
                    project_number="P-OLD",
                    drawing_number="S-1",
                    entities=[],
                    is_latest=False,
                ),
            ]
        )

        facets = await es_repo.aggregate_facets(
            ["project_number"], filters=BM25SearchFilter(latest_only=True)
        )

        values = {v for v, _ in facets["project_number"]}
        assert "P-CURRENT" in values
        assert "P-OLD" not in values

    async def test_no_fields_requested_is_not_an_error(self, corpus):
        assert await corpus.aggregate_facets([]) == {}

    async def test_an_unknown_field_degrades_rather_than_failing(self, corpus):
        """An empty facet list means "no filters offered". A 500 would take
        the sidebar, and the page around it, down."""
        facets = await corpus.aggregate_facets(["not_a_field"])

        assert facets == {} or facets.get("not_a_field") == []
