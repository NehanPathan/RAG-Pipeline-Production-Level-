from __future__ import annotations

import re
import statistics
from dataclasses import dataclass

from src.ingestion.chunkers.structure_chunker import StructuralSection
from src.ingestion.embedders.base import EmbeddingProvider

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass
class SemanticSegment:
    text: str
    page_number: int | None = None
    section_title: str | None = None
    heading_level: int | None = None
    is_table: bool = False
    # Inherited from the parent StructuralSection. Sentence-level splitting
    # has no finer confidence signal to offer, so every segment derived from
    # a section carries that section's figure.
    ocr_confidence: float | None = None


class SemanticChunker:
    """Part 4, Step 2: inside each StructuralSection, detects topic
    boundaries between sentences using cosine similarity on sentence
    embeddings, with an **adaptive** threshold rather than a fixed one --
    `threshold = mean(similarities) - std_multiplier * stdev(similarities)`.
    A similarity drop below that section's own mean/stdev marks a boundary,
    so the cut sensitivity adapts to how topically varied each section
    already is, instead of one absolute number applied everywhere.

    Tables pass through untouched (splitting a table's markdown mid-row
    would corrupt it). Sections below `min_sentences_for_split` skip
    embedding entirely -- there's no boundary to detect in 1-2 sentences.
    """

    def __init__(
        self,
        embedding_provider: EmbeddingProvider,
        std_multiplier: float = 1.0,
        min_sentences_for_split: int = 3,
    ) -> None:
        self._embedding_provider = embedding_provider
        self._std_multiplier = std_multiplier
        self._min_sentences_for_split = min_sentences_for_split

    async def split(self, sections: list[StructuralSection]) -> list[SemanticSegment]:
        segments: list[SemanticSegment] = []
        for section in sections:
            segments.extend(await self._split_section(section))
        return segments

    async def _split_section(self, section: StructuralSection) -> list[SemanticSegment]:
        if section.is_table:
            return [
                SemanticSegment(
                    text=section.text,
                    page_number=section.page_number,
                    section_title=section.section_title,
                    heading_level=section.heading_level,
                    is_table=True,
                    ocr_confidence=section.ocr_confidence,
                )
            ]

        sentences = self._split_sentences(section.text)
        if len(sentences) < self._min_sentences_for_split:
            return [
                SemanticSegment(
                    text=section.text,
                    page_number=section.page_number,
                    section_title=section.section_title,
                    heading_level=section.heading_level,
                    ocr_confidence=section.ocr_confidence,
                )
            ]

        embeddings = await self._embedding_provider.embed_texts(sentences)
        boundaries = self._detect_boundaries(embeddings)

        segments: list[SemanticSegment] = []
        start = 0
        for boundary in [*boundaries, len(sentences)]:
            chunk_text = " ".join(sentences[start:boundary]).strip()
            if chunk_text:
                segments.append(
                    SemanticSegment(
                        text=chunk_text,
                        page_number=section.page_number,
                        section_title=section.section_title,
                        heading_level=section.heading_level,
                        ocr_confidence=section.ocr_confidence,
                    )
                )
            start = boundary
        return segments

    def _split_sentences(self, text: str) -> list[str]:
        return [s for s in _SENTENCE_SPLIT_RE.split(text.strip()) if s.strip()]

    def _detect_boundaries(self, embeddings: list[list[float]]) -> list[int]:
        similarities = [
            self._cosine_similarity(embeddings[i], embeddings[i + 1])
            for i in range(len(embeddings) - 1)
        ]
        if not similarities:
            return []

        mean = statistics.mean(similarities)
        stdev = statistics.pstdev(similarities) if len(similarities) > 1 else 0.0
        threshold = mean - self._std_multiplier * stdev

        return [i + 1 for i, sim in enumerate(similarities) if sim < threshold]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
