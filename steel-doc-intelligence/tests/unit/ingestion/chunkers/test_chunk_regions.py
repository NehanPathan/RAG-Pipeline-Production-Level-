"""How honest a chunk is about where it came from.

A rectangle that bounds a whole section, attached to a chunk holding a
quarter of it, points at text the chunk does not contain. `region_precision`
is what stops a viewer drawing a tight box around the wrong words.
"""

from __future__ import annotations

import uuid

from src.domain.value_objects.provenance import (
    PRECISION_BLOCK,
    PRECISION_PAGE,
    PRECISION_SECTION,
    SPACE_SHEET,
    Region,
)
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker


def a_region() -> Region:
    return Region(page_number=1, x0=0.1, y0=0.2, x1=0.3, y1=0.25, space=SPACE_SHEET)


def chunker(parent: int = 400, child: int = 200) -> ParentChildChunker:
    return ParentChildChunker(
        ChunkingConfig(parent_chunk_size=parent, child_chunk_size=child, overlap=0)
    )


class TestPrecisionIsNotOverstated:
    def test_an_unsplit_section_keeps_block_precision(self):
        """A CAD callout, a title block or any short section survives the
        token split intact, so its rectangle still bounds it exactly.
        Downgrading here would leave a viewer drawing a soft box around text
        we can locate precisely."""
        chunks = chunker().chunk_section(
            uuid.uuid4(),
            'The annotation "BENT PLATE 5x5x 12 GA." labels a S-SECT_STEEL_THRU entity.',
            page_number=1,
            regions=[a_region()],
            region_precision=PRECISION_BLOCK,
        )
        assert chunks
        assert all(c.chunk_metadata.region_precision == PRECISION_BLOCK for c in chunks)

    def test_a_split_section_is_downgraded(self):
        """Once the text is cut, the rectangle bounds words the chunk no
        longer holds, and a token offset has no coordinate to recover from."""
        long_text = " ".join(f"sentence number {i} about steel framing." for i in range(300))
        chunks = chunker(parent=60, child=30).chunk_section(
            uuid.uuid4(),
            long_text,
            page_number=1,
            regions=[a_region()],
            region_precision=PRECISION_BLOCK,
        )
        assert len(chunks) > 2
        assert all(c.chunk_metadata.region_precision == PRECISION_SECTION for c in chunks)

    def test_no_regions_means_page_precision(self):
        chunks = chunker().chunk_section(uuid.uuid4(), "Some prose.", page_number=4)
        assert chunks
        assert all(c.chunk_metadata.region_precision == PRECISION_PAGE for c in chunks)
        assert all(c.chunk_metadata.regions == [] for c in chunks)


class TestRegionsReachEveryChunk:
    def test_parents_and_children_both_carry_them(self):
        chunks = chunker().chunk_section(
            uuid.uuid4(), "A short section.", page_number=1, regions=[a_region()]
        )
        assert chunks
        for chunk in chunks:
            assert chunk.chunk_metadata.regions == [a_region()]

    def test_each_chunk_gets_its_own_list(self):
        """Sharing one list would let a later mutation rewrite the provenance
        of every chunk cut from the same section."""
        chunks = chunker().chunk_section(
            uuid.uuid4(), "A short section.", page_number=1, regions=[a_region()]
        )
        first = chunks[0].chunk_metadata.regions
        first.append(Region(page_number=1, x0=0, y0=0, x1=1, y1=1))
        assert len(chunks[-1].chunk_metadata.regions) == 1

    def test_existing_callers_are_unaffected(self):
        """`regions` is keyword-only with a default, so every pre-existing
        call site keeps working."""
        chunks = chunker().chunk_section(uuid.uuid4(), "Prose.", page_number=2)
        assert chunks
        assert chunks[0].chunk_metadata.page_number == 2
