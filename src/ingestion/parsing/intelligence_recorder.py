from __future__ import annotations

import uuid

from src.domain.entities.document import DocumentChunk
from src.domain.repositories.document_intelligence_repository import DocumentIntelligenceRepository
from src.domain.value_objects.document_intelligence import (
    DocumentIntelligenceSummary,
    LayoutSummary,
    SimilarityEdge,
)
from src.ingestion.layout.models import OutlineNode
from src.ingestion.parsing.parsed_document import ParsedDocument


class DocumentIntelligenceRecorder:
    """Satisfies IngestionPipeline's `IntelligenceRecorder` Protocol: builds
    a domain-level `DocumentIntelligenceSummary` from a `ParsedDocument` +
    the chunks just written, then delegates persistence to the
    domain `DocumentIntelligenceRepository` port.

    Kept separate from that port so `DocumentIntelligenceRepository`
    (domain/) never needs to import `ParsedDocument`/`DocumentChunk`
    (ingestion-layer types) -- only this adapter does the translation.
    """

    def __init__(self, repository: DocumentIntelligenceRepository, max_graph_edges: int = 200) -> None:
        self._repository = repository
        self._max_graph_edges = max_graph_edges

    async def save(
        self,
        document_id: uuid.UUID,
        parsed_document: ParsedDocument,
        chunks: list[DocumentChunk],
        chunking_model_id: str,
        retrieval_model_id: str,
    ) -> None:
        ocr_metadata = parsed_document.ocr_metadata
        layout = parsed_document.layout

        summary = DocumentIntelligenceSummary(
            document_id=document_id,
            ocr_engine=ocr_metadata.engine,
            ocr_ran=ocr_metadata.ran,
            ocr_confidence_avg=ocr_metadata.confidence,
            ocr_processing_time_ms=ocr_metadata.processing_time_ms,
            ocr_language=ocr_metadata.language,
            embedding_model_chunking=chunking_model_id,
            embedding_model_retrieval=retrieval_model_id,
            layout=LayoutSummary(
                headings=[
                    {"text": h.text, "level": h.level, "page_number": h.page_number}
                    for h in layout.headings
                ],
                outline=[self._outline_to_dict(node) for node in layout.outline],
                tables_count=len(layout.tables),
                figures_count=len(layout.figures),
                lists_count=len(layout.lists),
                forms_count=len(layout.forms),
                footnotes_count=len(layout.footnotes),
            ),
            semantic_graph=self._build_semantic_graph(chunks),
        )
        await self._repository.save(summary)

    def _outline_to_dict(self, node: OutlineNode) -> dict:
        return {
            "title": node.title,
            "level": node.level,
            "page_number": node.page_number,
            "children": [self._outline_to_dict(child) for child in node.children],
        }

    def _build_semantic_graph(self, chunks: list[DocumentChunk]) -> list[SimilarityEdge]:
        """Cosine similarity between consecutive chunks' already-computed
        retrieval embeddings (Step 4 of IngestionPipeline has already run by
        the time this is called) -- a real, cheap-to-compute similarity
        graph of what's actually indexed, rather than re-embedding anything
        just for this visualization (Part 7's "semantic similarity graph")."""
        embedded_chunks = sorted(
            (c for c in chunks if c.has_embedding()), key=lambda c: c.position
        )

        edges: list[SimilarityEdge] = []
        for a, b in zip(embedded_chunks, embedded_chunks[1:]):
            if len(edges) >= self._max_graph_edges:
                break
            similarity = self._cosine_similarity(a.embedding, b.embedding)
            edges.append(SimilarityEdge(chunk_id_a=a.id, chunk_id_b=b.id, similarity=similarity))
        return edges

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b, strict=True))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)
