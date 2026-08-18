from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from src.domain.entities.document import Document, DocumentChunk, DocumentStatus
from src.domain.repositories.document_repository import ChunkRepository, DocumentRepository
from src.domain.repositories.search_repository import SearchRepository
from src.domain.repositories.vector_repository import VectorRepository
from src.ingestion.chunkers.chunking_strategy import ChunkingStrategy
from src.ingestion.embedders.embedding_strategy import EmbeddingStrategy
from src.ingestion.enrichers.llm_enricher import LLMMetadataEnricher
from src.ingestion.loaders.base import DocumentLoader
from src.ingestion.parsing.parsed_document import ParsedDocument
from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class IntelligenceRecorder(Protocol):
    """Structural type satisfied by DocumentIntelligenceRepository (Module 9)
    -- kept as a local Protocol rather than importing that module directly,
    so IngestionPipeline has no hard dependency on the persistence-layer
    package that implements it."""

    async def save(
        self,
        document_id: uuid.UUID,
        parsed_document: ParsedDocument,
        chunks: list[DocumentChunk],
        chunking_model_id: str,
        retrieval_model_id: str,
    ) -> None: ...


@dataclass
class IngestionResult:
    document_id: uuid.UUID
    status: DocumentStatus
    chunks_created: int = 0
    error: str | None = None


class IngestionPipeline:
    """
    Orchestrates the full document ingestion flow:
    Load → Detect+OCR → Layout → Enrich → Hybrid Chunk → Embed → Store → Intelligence Record

    Phase 4A inserts OCR/layout parsing between Load and Enrich, and swaps
    the chunker/embedder for strategy objects (ChunkingStrategy,
    EmbeddingStrategy) so HybridChunkingPipeline/ParentChildOnlyStrategy and
    dual-role embedding can be swapped without touching this control flow
    (see docs/architecture/12_phase4a_design_review.md §6).
    """

    def __init__(
        self,
        loaders: list[DocumentLoader],
        enricher: LLMMetadataEnricher,
        parsing_service: DocumentParsingService,
        chunking_strategy: ChunkingStrategy,
        embedding_strategy: EmbeddingStrategy,
        document_repo: DocumentRepository,
        chunk_repo: ChunkRepository,
        vector_repo: VectorRepository,
        search_repo: SearchRepository,
        intelligence_recorder: IntelligenceRecorder | None = None,
    ) -> None:
        self._loaders = loaders
        self._enricher = enricher
        self._parsing_service = parsing_service
        self._chunking_strategy = chunking_strategy
        self._embedding_strategy = embedding_strategy
        self._document_repo = document_repo
        self._chunk_repo = chunk_repo
        self._vector_repo = vector_repo
        self._search_repo = search_repo
        self._intelligence_recorder = intelligence_recorder

    async def ingest(self, document: Document, file_path: Path) -> IngestionResult:
        logger.info("ingestion_start", document_id=str(document.id), file=document.file_name)

        document.mark_processing()
        await self._document_repo.update(document)

        try:
            loader = self._select_loader(document.file_type, file_path.suffix)
            raw_document = await loader.load(file_path)
            document.loader_used = loader.name

            # analysis, unified into one ParsedDocument (Parts 1-3)
            parsed_document = await self._parsing_service.process(
                raw_document, file_path, document.file_type
            )
            document.page_count = parsed_document.raw.page_count
            document.word_count = parsed_document.raw.word_count

            document.metadata = await self._enricher.enrich(
                content=parsed_document.full_text,
                file_name=document.file_name,
            )

            # ParentChildOnlyStrategy for the Part 8 A/B benchmark (Gap 5)
            chunks = await self._chunking_strategy.chunk(document.id, parsed_document)

            # Denormalize tenant/metadata fields onto each chunk so the
            # vector/search repositories can filter on them directly.
            for chunk in chunks:
                chunk.user_id = document.user_id
                chunk.domain = document.metadata.domain
                chunk.tags = document.metadata.tags
                chunk.file_type = document.file_type
                chunk.document_name = document.file_name
                # Governance MAP: classification is inherited, never inferred
                # per chunk. A document is classified once, at upload, and
                # every chunk derived from it carries that label into Qdrant
                # and Elasticsearch so retrieval can filter on it.
                chunk.sensitivity = document.sensitivity

            retrieval_provider = self._embedding_strategy.retrieval_provider
            child_chunks = [c for c in chunks if c.chunk_type.value in ("child", "table", "standalone")]
            texts = [c.content for c in child_chunks]
            embeddings = await retrieval_provider.embed_texts(texts)
            for chunk, embedding in zip(child_chunks, embeddings, strict=False):
                chunk.embedding = embedding
                chunk.embedding_model = retrieval_provider.model_id

            await self._vector_repo.create_collection_if_not_exists(
                retrieval_provider.dimensions
            )
            await self._search_repo.create_index_if_not_exists()

            saved_chunks = await self._chunk_repo.save_batch(chunks)

            chunks_with_embeddings = [c for c in saved_chunks if c.has_embedding()]
            await self._vector_repo.upsert_batch(chunks_with_embeddings)

            await self._search_repo.index_batch(saved_chunks)

            # (Part 7's Document Intelligence UI reads this) -- optional so
            # IngestionPipeline has no hard dependency on Module 9 being wired.
            if self._intelligence_recorder is not None:
                await self._intelligence_recorder.save(
                    document.id,
                    parsed_document,
                    saved_chunks,
                    chunking_model_id=self._embedding_strategy.chunking_provider.model_id,
                    retrieval_model_id=retrieval_provider.model_id,
                )

            document.mark_indexed()
            await self._document_repo.update(document)

            result = IngestionResult(
                document_id=document.id,
                status=DocumentStatus.INDEXED,
                chunks_created=len(saved_chunks),
            )
            logger.info(
                "ingestion_complete",
                document_id=str(document.id),
                chunks=len(saved_chunks),
                embeddings=len(chunks_with_embeddings),
            )
            return result

        except Exception as e:
            error_msg = str(e)
            logger.error("ingestion_failed", document_id=str(document.id), error=error_msg, exc_info=True)
            document.mark_failed(error_msg)
            await self._document_repo.update(document)
            return IngestionResult(
                document_id=document.id,
                status=DocumentStatus.FAILED,
                error=error_msg,
            )

    def _select_loader(self, file_type: str, extension: str) -> DocumentLoader:
        mime_map = {
            "pdf": "application/pdf",
            "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "txt": "text/plain",
            "md": "text/markdown",
            "html": "text/html",
            "png": "image/png",
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "tiff": "image/tiff",
            "tif": "image/tiff",
            "bmp": "image/bmp",
        }
        mime_type = mime_map.get(file_type.lower(), "application/octet-stream")

        for loader in self._loaders:
            if loader.supports(mime_type, f".{file_type.lower()}"):
                return loader

        # Fallback to last loader (should be unstructured)
        return self._loaders[-1]
