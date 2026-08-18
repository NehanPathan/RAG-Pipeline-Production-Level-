from __future__ import annotations

from dataclasses import dataclass

from src.ingestion.embedders.base import EmbeddingProvider


@dataclass
class EmbeddingStrategy:
    """Bundles the two embedding roles Part 6 requires: one model for
    chunking-time semantic similarity (SemanticChunker's topic-boundary
    detection), one for retrieval (what's actually stored in Qdrant and
    searched at query time). They may be the same provider or different ones
    (e.g. chunking="bge_m3", retrieval="openai") -- HybridChunkingPipeline
    and IngestionPipeline each read the role they need from this bundle
    instead of taking two separate constructor params.

    Extension point (not built now): a future EnsembleEmbeddingProvider could
    satisfy the same EmbeddingProvider contract by combining multiple
    providers (concatenation or weighted averaging) and be dropped into
    either role here with no other changes.
    """

    chunking_provider: EmbeddingProvider
    retrieval_provider: EmbeddingProvider
