import pytest

from src.ingestion.chunkers.semantic_chunker import SemanticChunker
from src.ingestion.chunkers.structure_chunker import StructuralSection


class FakeEmbeddingProvider:
    """Assigns orthogonal vectors by sentence prefix so cosine similarity
    is deterministic: all "A*" sentences are near-identical, all "B*"
    sentences are near-identical, and A/B are maximally dissimilar."""

    async def embed_texts(self, texts):
        return [[1.0, 0.0] if t.startswith("A") else [0.0, 1.0] for t in texts]

    async def embed_query(self, query):
        return [1.0, 0.0]

    @property
    def model_id(self) -> str:
        return "fake"

    @property
    def dimensions(self) -> int:
        return 2


async def test_topic_shift_detected_as_boundary():
    chunker = SemanticChunker(embedding_provider=FakeEmbeddingProvider(), std_multiplier=1.0)
    section = StructuralSection(text="A1. A2. A3. B1. B2.", page_number=1, section_title="Sec", heading_level=1)

    segments = await chunker.split([section])

    assert len(segments) == 2
    assert segments[0].text == "A1. A2. A3."
    assert segments[1].text == "B1. B2."
    assert all(s.section_title == "Sec" for s in segments)


async def test_uniform_topic_produces_single_segment():
    chunker = SemanticChunker(embedding_provider=FakeEmbeddingProvider(), std_multiplier=1.0)
    section = StructuralSection(text="A1. A2. A3. A4.", page_number=1)

    segments = await chunker.split([section])

    assert len(segments) == 1
    assert segments[0].text == "A1. A2. A3. A4."


async def test_short_section_skips_embedding_entirely():
    class ExplodingProvider(FakeEmbeddingProvider):
        async def embed_texts(self, texts):
            raise AssertionError("should not be called for short sections")

    chunker = SemanticChunker(embedding_provider=ExplodingProvider(), min_sentences_for_split=3)
    section = StructuralSection(text="One sentence only.", page_number=1)

    segments = await chunker.split([section])

    assert len(segments) == 1
    assert segments[0].text == "One sentence only."


async def test_table_sections_pass_through_untouched():
    class ExplodingProvider(FakeEmbeddingProvider):
        async def embed_texts(self, texts):
            raise AssertionError("tables must not be sentence-split or embedded")

    chunker = SemanticChunker(embedding_provider=ExplodingProvider())
    section = StructuralSection(text="| a | b |\n| 1 | 2 |", is_table=True, page_number=1)

    segments = await chunker.split([section])

    assert len(segments) == 1
    assert segments[0].is_table is True
    assert segments[0].text == section.text


def test_cosine_similarity_of_identical_vectors_is_one():
    assert SemanticChunker._cosine_similarity([1.0, 2.0], [1.0, 2.0]) == pytest.approx(1.0)


def test_cosine_similarity_of_zero_vector_is_zero():
    assert SemanticChunker._cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0
