from __future__ import annotations

from src.config import get_settings
from src.infrastructure.database.redis.connection import RedisCache, get_redis_client
from src.infrastructure.search.elasticsearch.repository import (
    ElasticsearchSearchRepository,
    create_elasticsearch_client,
)
from src.infrastructure.vector_store.qdrant.cache_repository import QdrantSemanticCacheRepository
from src.infrastructure.vector_store.qdrant.repository import (
    QdrantVectorRepository,
    create_qdrant_client,
)
from src.ingestion.embedders.openai_embedder import OpenAIEmbeddingProvider
from src.llm.registry import get_llm_provider
from src.retrieval.agents.filter_generator import FilterGenerator
from src.retrieval.agents.intent_classifier import IntentClassifier
from src.retrieval.agents.query_agent import QueryAgent
from src.retrieval.agents.query_expander import QueryExpander
from src.retrieval.agents.query_rewriter import QueryRewriter
from src.retrieval.agents.source_selector import SourceSelector
from src.retrieval.answer.answer_pipeline import AnswerPipeline
from src.retrieval.answer.citation_validator import CitationValidator
from src.retrieval.answer.context_assembler import ContextAssembler
from src.retrieval.answer.prompt_builder import PromptBuilder
from src.retrieval.answer.stream_generator import StreamGenerator
from src.retrieval.cache.semantic_cache import SemanticCache
from src.retrieval.context.citation_preserver import CitationPreserver
from src.retrieval.context.context_compressor import ContextCompressor
from src.retrieval.context.context_deduplicator import ContextDeduplicator
from src.retrieval.context.context_processor import ContextProcessor
from src.retrieval.context.token_budget_manager import TokenBudgetManager
from src.retrieval.context.token_counter import TokenCounter
from src.retrieval.fusers.duplicate_remover import DuplicateRemover
from src.retrieval.fusers.fuser import Fuser
from src.retrieval.fusers.rrf_fuser import RRFFusion
from src.retrieval.fusers.score_normalizer import ScoreNormalizer
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.pipeline import QueryPipeline
from src.retrieval.rerankers.registry import get_reranker
from src.retrieval.searchers.bm25_searcher import BM25Searcher
from src.retrieval.searchers.vector_searcher import VectorSearcher

_query_pipeline: QueryPipeline | None = None


def get_query_pipeline() -> QueryPipeline:
    """Singleton QueryPipeline, built from current settings on first access.

    Manual construction rather than the `dependency-injector` package
    (pinned in pyproject.toml but not actually used anywhere in this
    project) -- every dependency built here is stateless/connection-pooled,
    so there's no reason to rebuild the graph per request, and a plain
    module-level singleton (matching get_settings()/get_redis_client()/
    get_langfuse_client()'s existing pattern) is simpler than introducing
    a DI container for the first time in this PR.
    """
    global _query_pipeline
    if _query_pipeline is None:
        _query_pipeline = _build_query_pipeline()
    return _query_pipeline


def reset_query_pipeline() -> None:
    """Test helper: force re-construction of the singleton on next access."""
    global _query_pipeline
    _query_pipeline = None


def _build_query_pipeline() -> QueryPipeline:
    settings = get_settings()

    small_llm = get_llm_provider(settings, role="small")
    large_llm = get_llm_provider(settings, role="large")

    embedder = OpenAIEmbeddingProvider(
        api_key=settings.openai_api_key,
        model=settings.openai_embedding_model,
        dimensions=settings.openai_embedding_dimensions,
        batch_size=settings.embedding_batch_size,
        cache=RedisCache(get_redis_client()),
        cache_ttl=settings.redis_ttl_embedding,
    )

    vector_repo = QdrantVectorRepository(
        client=create_qdrant_client(), collection_name=settings.qdrant_collection_name
    )
    search_repo = ElasticsearchSearchRepository(
        client=create_elasticsearch_client(), index_name=settings.elasticsearch_index_name
    )
    cache_repo = QdrantSemanticCacheRepository(
        client=create_qdrant_client(), collection_name=settings.qdrant_cache_collection_name
    )

    query_agent = QueryAgent(
        rewriter=QueryRewriter(small_llm),
        expander=QueryExpander(small_llm),
        classifier=IntentClassifier(small_llm),
        source_selector=SourceSelector(),
        filter_generator=FilterGenerator(small_llm),
        expansion_count=settings.query_expansion_count,
    )

    hybrid_retriever = HybridRetriever(
        vector_searcher=VectorSearcher(vector_repo=vector_repo, embedding_provider=embedder),
        bm25_searcher=BM25Searcher(search_repo=search_repo),
    )

    fuser = Fuser(
        rrf=RRFFusion(k=settings.rrf_k),
        normalizer=ScoreNormalizer(),
        deduplicator=DuplicateRemover(),
    )

    reranker = get_reranker(settings)

    context_processor = ContextProcessor(
        deduplicator=ContextDeduplicator(),
        compressor=ContextCompressor(llm_provider=small_llm),
        budget_manager=TokenBudgetManager(
            token_counter=TokenCounter(model=settings.openai_large_model),
            max_tokens=settings.context_max_tokens,
        ),
        citation_preserver=CitationPreserver(),
    )

    answer_pipeline = AnswerPipeline(
        context_assembler=ContextAssembler(),
        prompt_builder=PromptBuilder(),
        stream_generator=StreamGenerator(llm_provider=large_llm),
        citation_validator=CitationValidator(),
    )

    semantic_cache = SemanticCache(
        repository=cache_repo,
        embedding_provider=embedder,
        score_threshold=settings.semantic_cache_score_threshold,
    )

    return QueryPipeline(
        query_agent=query_agent,
        hybrid_retriever=hybrid_retriever,
        fuser=fuser,
        reranker=reranker,
        context_processor=context_processor,
        answer_pipeline=answer_pipeline,
        semantic_cache=semantic_cache,
        vector_top_k=settings.vector_search_top_k,
        bm25_top_k=settings.bm25_search_top_k,
        rerank_top_n=settings.rerank_top_n,
    )
