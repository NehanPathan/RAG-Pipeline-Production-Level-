from __future__ import annotations

from typing import Any, Literal

from src.config import Settings
from src.ingestion.embedders.base import EmbeddingProvider
from src.ingestion.embedders.langchain_embedder import LangChainEmbeddingProvider

EmbeddingRole = Literal["chunking", "retrieval"]

# Local models are multi-hundred-MB downloads, so each is constructed once per
# process no matter how many times the registry is called. The cache is keyed
# by model name rather than by role: the same model requested for two roles is
# one model.
_local_models: dict[str, Any] = {}


def get_embedding_provider(
    settings: Settings, role: EmbeddingRole, cache: Any = None
) -> EmbeddingProvider:
    """Select and construct the configured EmbeddingProvider for a given role.

    `role="chunking"` is used by HybridChunkingPipeline's SemanticChunker for
    topic-boundary detection; `role="retrieval"` is what actually gets stored
    in Qdrant and searched at query time. They may be different models --
    exact mirror of `llm/registry.py`'s role-based factory.

    Each branch now returns a LangChain `Embeddings` wrapped in
    `LangChainEmbeddingProvider`. The role split, the Redis cache and the
    cache-hit metric are unchanged.
    """
    provider_name = (
        settings.chunking_embedding_provider if role == "chunking" else settings.embedding_provider
    )

    if provider_name == "openai":
        from langchain_openai import OpenAIEmbeddings

        return LangChainEmbeddingProvider(
            OpenAIEmbeddings(
                model=settings.openai_embedding_model,
                dimensions=settings.openai_embedding_dimensions,
                api_key=settings.openai_api_key or None,
                chunk_size=settings.embedding_batch_size,
            ),
            model_id=settings.openai_embedding_model,
            dimensions=settings.openai_embedding_dimensions,
            cache=cache,
            cache_ttl=settings.redis_ttl_embedding,
        )

    if provider_name == "bge_m3":
        return LangChainEmbeddingProvider(
            _local_embeddings(settings.bge_model_name),
            model_id=settings.bge_model_name,
            dimensions=settings.bge_embedding_dimensions,
            cache=cache,
            cache_ttl=settings.redis_ttl_embedding,
        )

    if provider_name == "e5_large":
        # E5 is trained with an instruction-prefix convention that materially
        # affects retrieval quality; omitting it degrades results silently
        # rather than erroring. See LangChainEmbeddingProvider.
        return LangChainEmbeddingProvider(
            _local_embeddings(settings.e5_model_name),
            model_id=settings.e5_model_name,
            dimensions=settings.e5_embedding_dimensions,
            cache=cache,
            cache_ttl=settings.redis_ttl_embedding,
            query_prefix="query: ",
            document_prefix="passage: ",
        )

    raise ValueError(f"Unsupported embedding provider {provider_name!r} for role {role!r}")


def _local_embeddings(model_name: str) -> Any:
    """Load (and process-cache) a local sentence-transformers model."""
    from langchain_huggingface import HuggingFaceEmbeddings

    if model_name not in _local_models:
        _local_models[model_name] = HuggingFaceEmbeddings(
            model_name=model_name,
            encode_kwargs={"normalize_embeddings": True},
        )
    return _local_models[model_name]


def reset_local_model_cache() -> None:
    """Test helper: drop process-cached local models."""
    _local_models.clear()


def build_embedding_strategy(settings: Settings, cache: Any = None):
    """Convenience constructor: bundles both roles into one EmbeddingStrategy,
    the shape `_build_ingestion_pipeline()` consumes (Module 8)."""
    from src.ingestion.embedders.embedding_strategy import EmbeddingStrategy

    return EmbeddingStrategy(
        chunking_provider=get_embedding_provider(settings, role="chunking", cache=cache),
        retrieval_provider=get_embedding_provider(settings, role="retrieval", cache=cache),
    )
