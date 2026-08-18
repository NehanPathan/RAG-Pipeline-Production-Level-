from __future__ import annotations

from typing import Any, Literal

from src.config import Settings
from src.ingestion.embedders.base import EmbeddingProvider
from src.ingestion.embedders.openai_embedder import OpenAIEmbeddingProvider

EmbeddingRole = Literal["chunking", "retrieval"]

_bge_model: Any = None
_e5_model: Any = None


def get_embedding_provider(settings: Settings, role: EmbeddingRole, cache: Any = None) -> EmbeddingProvider:
    """Select and construct the configured EmbeddingProvider for a given role.

    `role="chunking"` is used by HybridChunkingPipeline's SemanticChunker for
    topic-boundary detection; `role="retrieval"` is what actually gets stored
    in Qdrant and searched at query time. They may be different models --
    exact mirror of `llm/registry.py`'s role-based factory.

    `retrieval` reuses `settings.embedding_provider` (the existing setting
    `_get_embedder()` in `api/dependencies.py` already reads) so this is a
    pure addition -- nothing about the existing retrieval embedding wiring
    changes until Module 8 rewires `_build_ingestion_pipeline()` to use
    `EmbeddingStrategy`. `chunking` is a new, separate setting since it
    defaults to a different (local) provider.
    """
    provider_name = (
        settings.chunking_embedding_provider if role == "chunking" else settings.embedding_provider
    )

    if provider_name == "openai":
        return OpenAIEmbeddingProvider(
            api_key=settings.openai_api_key,
            model=settings.openai_embedding_model,
            dimensions=settings.openai_embedding_dimensions,
            batch_size=settings.embedding_batch_size,
            cache=cache,
            cache_ttl=settings.redis_ttl_embedding,
        )

    if provider_name == "bge_m3":
        from src.ingestion.embedders.bge_embedder import BGEEmbeddingProvider

        return BGEEmbeddingProvider(
            model=_load_sentence_transformer(settings.bge_model_name, cache_slot="bge"),
            model_id=settings.bge_model_name,
        )

    if provider_name == "e5_large":
        from src.ingestion.embedders.e5_embedder import E5EmbeddingProvider

        return E5EmbeddingProvider(
            model=_load_sentence_transformer(settings.e5_model_name, cache_slot="e5"),
            model_id=settings.e5_model_name,
        )

    raise ValueError(f"Unsupported embedding provider {provider_name!r} for role {role!r}")


def _load_sentence_transformer(model_name: str, cache_slot: str) -> Any:
    """Loads (and caches, module-level) a local SentenceTransformer model --
    these are multi-hundred-MB downloads, so the registry only constructs
    each one once per process regardless of how many times it's called."""
    global _bge_model, _e5_model
    from sentence_transformers import SentenceTransformer

    if cache_slot == "bge":
        if _bge_model is None:
            _bge_model = SentenceTransformer(model_name)
        return _bge_model

    if _e5_model is None:
        _e5_model = SentenceTransformer(model_name)
    return _e5_model


def build_embedding_strategy(settings: Settings, cache: Any = None):
    """Convenience constructor: bundles both roles into one EmbeddingStrategy,
    the shape `_build_ingestion_pipeline()` consumes (Module 8)."""
    from src.ingestion.embedders.embedding_strategy import EmbeddingStrategy

    return EmbeddingStrategy(
        chunking_provider=get_embedding_provider(settings, role="chunking", cache=cache),
        retrieval_provider=get_embedding_provider(settings, role="retrieval", cache=cache),
    )
