from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from src.domain.entities.document import Document, DocumentStatus
from src.domain.repositories.document_repository import ChunkRepository, DocumentRepository
from src.domain.repositories.search_repository import SearchRepository
from src.domain.repositories.vector_repository import VectorRepository
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker
from src.ingestion.embedders.base import EmbeddingProvider
from src.ingestion.enrichers.llm_enricher import LLMMetadataEnricher
from src.ingestion.loaders.base import DocumentLoader
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


@dataclass
class IngestionResult:
    document_id: uuid.UUID
    status: DocumentStatus
    chunks_created: int = 0
    error: str | None = None


class IngestionPipeline:
    """
    Orchestrates the full document ingestion flow:
    Load → Enrich → Chunk → Embed → Store (Qdrant + ES + Postgres)
    """

    def __init__(
        self,
        loaders: list[DocumentLoader],
        enricher: LLMMetadataEnricher,
        chunker: ParentChildChunker,
        embedding_provider: EmbeddingProvider,
        document_repo: DocumentRepository,
        chunk_repo: ChunkRepository,
        vector_repo: VectorRepository,
        search_repo: SearchRepository,
    ) -> None:
        self._loaders = loaders
        self._enricher = enricher
        self._chunker = chunker
        self._embedding_provider = embedding_provider
        self._document_repo = document_repo
        self._chunk_repo = chunk_repo
        self._vector_repo = vector_repo
        self._search_repo = search_repo

    async def ingest(self, document: Document, file_path: Path) -> IngestionResult:
        logger.info("ingestion_start", document_id=str(document.id), file=document.file_name)

        # Mark as processing
        document.mark_processing()
        await self._document_repo.update(document)

        try:
            # Step 1: Select loader and load document
            loader = self._select_loader(document.file_type, file_path.suffix)
            raw_document = await loader.load(file_path)
            document.loader_used = loader.name
            document.page_count = raw_document.page_count
            document.word_count = raw_document.word_count

            # Step 2: LLM metadata enrichment
            document.metadata = await self._enricher.enrich(
                content=raw_document.full_text,
                file_name=document.file_name,
            )

            # Step 3: Chunking (parent + child + tables)
            chunks = self._chunker.chunk(document.id, raw_document)

            # Denormalize tenant/metadata fields onto each chunk so the
            # vector/search repositories can filter on them directly.
            for chunk in chunks:
                chunk.user_id = document.user_id
                chunk.domain = document.metadata.domain
                chunk.tags = document.metadata.tags
                chunk.file_type = document.file_type
                chunk.document_name = document.file_name

            # Step 4: Generate embeddings in batch
            child_chunks = [c for c in chunks if c.chunk_type.value in ("child", "table", "standalone")]
            texts = [c.content for c in child_chunks]
            embeddings = await self._embedding_provider.embed_texts(texts)
            for chunk, embedding in zip(child_chunks, embeddings):
                chunk.embedding = embedding
                chunk.embedding_model = self._embedding_provider.model_id

            # Step 5: Ensure collection exists
            await self._vector_repo.create_collection_if_not_exists(
                self._embedding_provider.dimensions
            )
            await self._search_repo.create_index_if_not_exists()

            # Step 6: Store chunks in Postgres
            saved_chunks = await self._chunk_repo.save_batch(chunks)

            # Step 7: Upsert vectors to Qdrant (only chunks with embeddings)
            chunks_with_embeddings = [c for c in saved_chunks if c.has_embedding()]
            await self._vector_repo.upsert_batch(chunks_with_embeddings)

            # Step 8: Index in Elasticsearch for BM25
            await self._search_repo.index_batch(saved_chunks)

            # Mark as indexed
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
        }
        mime_type = mime_map.get(file_type.lower(), "application/octet-stream")

        for loader in self._loaders:
            if loader.supports(mime_type, f".{file_type.lower()}"):
                return loader

        # Fallback to last loader (should be unstructured)
        return self._loaders[-1]
