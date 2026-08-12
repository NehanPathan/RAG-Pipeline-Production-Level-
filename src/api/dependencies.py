from __future__ import annotations

from src.config import get_settings
from src.evaluation.online.feedback import FeedbackService
from src.evaluation.online.sampler import OnlineEvaluator
from src.governance.policy import PolicyViolationError, get_policy
from src.governance.sensitivity_guard import SensitivityGuard
from src.infrastructure.database.postgres.chunk_repository import PostgresChunkRepository
from src.infrastructure.database.postgres.connection import get_session_factory
from src.infrastructure.database.postgres.conversation_repository import (
    PostgresConversationRepository,
)
from src.infrastructure.database.postgres.document_intelligence_repository import (
    PostgresDocumentIntelligenceRepository,
)
from src.infrastructure.database.postgres.document_repository import PostgresDocumentRepository
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
from src.ingestion.chunkers.chunk_validator import ChunkValidator
from src.ingestion.chunkers.chunking_strategy import ChunkingStrategy
from src.ingestion.chunkers.hybrid_chunking_pipeline import HybridChunkingPipeline
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker
from src.ingestion.chunkers.semantic_chunker import SemanticChunker
from src.ingestion.chunkers.structure_chunker import StructureChunker
from src.ingestion.embedders.base import EmbeddingProvider
from src.ingestion.embedders.embedding_strategy import EmbeddingStrategy
from src.ingestion.embedders.registry import get_embedding_provider
from src.ingestion.enrichers.domain_enricher import DomainMetadataEnricher
from src.ingestion.enrichers.llm_enricher import LLMMetadataEnricher
from src.ingestion.extractors.regex_extractor import RegexSteelEntityExtractor
from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer
from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer
from src.ingestion.loaders.docling_loader import DoclingLoader
from src.ingestion.loaders.image_loader import ImagePassthroughLoader
from src.ingestion.loaders.unstructured_loader import UnstructuredLoader
from src.ingestion.ocr.detector import OCRDetector
from src.ingestion.ocr.registry import get_ocr_provider
from src.ingestion.parsing.intelligence_recorder import DocumentIntelligenceRecorder
from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService
from src.ingestion.pipeline import IngestionPipeline
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
from src.routing.classifier import RouteClassifier
from src.routing.router import QueryRouter
from src.tools.registry import load_builtin_tools

# DEFAULT_USER_ID now lives in src/governance/rbac.py: the auth seam should
# not depend on the retrieval object graph this module builds, and importing
# it from here pulled that whole graph into anything that needed the constant.

_embedder: EmbeddingProvider | None = None
_vector_repo: QdrantVectorRepository | None = None
_search_repo: ElasticsearchSearchRepository | None = None
_cache_repo: QdrantSemanticCacheRepository | None = None


def _get_embedder() -> EmbeddingProvider:
    """The retrieval-role embedding provider, as one process-wide singleton.

    Built through the registry rather than constructed directly, so the
    retrieval role honours `EMBEDDING_PROVIDER` like every other caller. The
    same instance is handed to VectorSearcher and SemanticCache: two
    instances of *different* models would put the query and the stored
    chunks in different embedding spaces, and the only symptom would be
    quietly poor retrieval.
    """
    global _embedder
    if _embedder is None:
        settings = get_settings()
        _embedder = get_embedding_provider(
            settings, role="retrieval", cache=RedisCache(get_redis_client())
        )
    return _embedder


def _get_vector_repo() -> QdrantVectorRepository:
    global _vector_repo
    if _vector_repo is None:
        settings = get_settings()
        _vector_repo = QdrantVectorRepository(
            client=create_qdrant_client(), collection_name=settings.qdrant_collection_name
        )
    return _vector_repo


def _get_search_repo() -> ElasticsearchSearchRepository:
    global _search_repo
    if _search_repo is None:
        settings = get_settings()
        _search_repo = ElasticsearchSearchRepository(
            client=create_elasticsearch_client(), index_name=settings.elasticsearch_index_name
        )
    return _search_repo


def _get_cache_repo() -> QdrantSemanticCacheRepository:
    global _cache_repo
    if _cache_repo is None:
        settings = get_settings()
        _cache_repo = QdrantSemanticCacheRepository(
            client=create_qdrant_client(), collection_name=settings.qdrant_cache_collection_name
        )
    return _cache_repo


async def ensure_cache_collection() -> None:
    """Create the semantic-query-cache Qdrant collection if missing.

    Unlike `document_chunks`, nothing in the chat path creates this
    collection on demand (the cache is read-before-write on every query),
    so it must be ensured once at startup instead.
    """
    await _get_cache_repo().create_collection_if_not_exists(_get_embedder().dimensions)


async def ensure_search_schema() -> None:
    """Bring both search backends' schemas up to date on an existing deployment.

    Both backends short-circuit their creation calls once the collection or
    index exists, so neither notices a field added later:

    * Qdrant's `create_collection_if_not_exists` only indexes payload fields
      when it actually creates the collection, so a pre-existing
      `document_chunks` would run the classification filter as a full scan.
    * Elasticsearch's `create_index_if_not_exists` returns early when the
      index is there, so an edited `INDEX_MAPPINGS` never reaches a
      deployment that has already ingested a document.

    Both are silent failures — correct-looking results, wrong performance or
    a mis-typed field — so they are repaired explicitly at startup.
    """
    await _get_vector_repo().ensure_payload_indexes()
    await _get_search_repo().ensure_mapping()


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

    embedder = _get_embedder()
    vector_repo = _get_vector_repo()
    search_repo = _get_search_repo()
    cache_repo = _get_cache_repo()

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

    policy = get_policy()

    # GOVERN (C-GOV-01/02): fail loudly at construction if configuration
    # points at an unapproved model. Catching this at startup rather than on
    # the first request means an unreviewed model swap cannot quietly serve
    # traffic until someone notices in a dashboard a week later.
    for provider_name in {settings.small_llm_provider, settings.large_llm_provider}:
        decision = policy.check_llm_provider(provider_name)
        if decision.denied:
            raise PolicyViolationError(decision)

    embedding_decision = policy.check_embedding_model(embedder.model_id)
    if embedding_decision.denied:
        raise PolicyViolationError(embedding_decision)

    # Tools must be registered before the router is built: the classifier's
    # prompt is generated from the live registry, so an empty registry would
    # produce a router that never selects a tool.
    load_builtin_tools()

    router = QueryRouter(
        classifier=RouteClassifier(
            llm_provider=small_llm, min_confidence=settings.router_min_confidence
        ),
        rules_only=settings.router_rules_only,
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
        sensitivity_guard=SensitivityGuard(policy.default_sensitivity),
        policy=policy,
        router=router,
        # General-knowledge answers use the large model: they carry no
        # retrieved context, so the model's own quality is all there is.
        direct_llm=large_llm,
    )


_conversation_repository: PostgresConversationRepository | None = None
_feedback_service: FeedbackService | None = None
_online_evaluator: OnlineEvaluator | None = None


def get_conversation_repository() -> PostgresConversationRepository:
    global _conversation_repository
    if _conversation_repository is None:
        _conversation_repository = PostgresConversationRepository(get_session_factory())
    return _conversation_repository


def get_feedback_service() -> FeedbackService:
    global _feedback_service
    if _feedback_service is None:
        _feedback_service = FeedbackService(
            session_factory=get_session_factory(),
            conversation_repo=get_conversation_repository(),
        )
    return _feedback_service


def get_online_evaluator() -> OnlineEvaluator:
    """Judge for sampled live traffic.

    Uses the `small` LLM role: online scoring runs on a fraction of every
    request, so a large-model judge would cost more than the answers it
    grades. The offline runner uses the same role, keeping the two sets of
    numbers on one scale.
    """
    global _online_evaluator
    if _online_evaluator is None:
        settings = get_settings()
        _online_evaluator = OnlineEvaluator(
            scorer_llm=get_llm_provider(settings, role="small"),
            session_factory=get_session_factory(),
            sample_rate=settings.governance_online_eval_sample_rate,
        )
    return _online_evaluator


_document_repository: PostgresDocumentRepository | None = None
_chunk_repository: PostgresChunkRepository | None = None
_intelligence_repository: PostgresDocumentIntelligenceRepository | None = None
_ingestion_pipeline: IngestionPipeline | None = None


def get_document_repository() -> PostgresDocumentRepository:
    global _document_repository
    if _document_repository is None:
        _document_repository = PostgresDocumentRepository(get_session_factory())
    return _document_repository


def get_chunk_repository() -> PostgresChunkRepository:
    global _chunk_repository
    if _chunk_repository is None:
        _chunk_repository = PostgresChunkRepository(get_session_factory())
    return _chunk_repository


def get_intelligence_repository() -> PostgresDocumentIntelligenceRepository:
    global _intelligence_repository
    if _intelligence_repository is None:
        _intelligence_repository = PostgresDocumentIntelligenceRepository(get_session_factory())
    return _intelligence_repository


def get_vector_repository() -> QdrantVectorRepository:
    return _get_vector_repo()


def get_search_repository() -> ElasticsearchSearchRepository:
    return _get_search_repo()


def get_ingestion_pipeline() -> IngestionPipeline:
    """Singleton IngestionPipeline, mirroring get_query_pipeline()'s pattern."""
    global _ingestion_pipeline
    if _ingestion_pipeline is None:
        _ingestion_pipeline = _build_ingestion_pipeline()
    return _ingestion_pipeline


def _build_ingestion_pipeline() -> IngestionPipeline:
    settings = get_settings()
    small_llm = get_llm_provider(settings, role="small")

    parent_child_chunker = ParentChildChunker(
        ChunkingConfig(
            parent_chunk_size=settings.parent_chunk_size,
            child_chunk_size=settings.child_chunk_size,
            overlap=settings.chunk_overlap,
        )
    )

    # `retrieval_provider` reuses the existing `_get_embedder()` singleton
    # (the same instance VectorSearcher/SemanticCache embed queries with) so
    # ingestion and query time never drift onto different embedding spaces.
    # `chunking_provider` is a separate, new role (Part 6) -- only used for
    # SemanticChunker's topic-boundary detection, never stored in Qdrant.
    embedding_strategy = EmbeddingStrategy(
        chunking_provider=get_embedding_provider(settings, role="chunking"),
        retrieval_provider=_get_embedder(),
    )

    # One extractor instance for both the document-level enricher and the
    # per-chunk pass: it compiles ~40 patterns and parses the gazetteer, and
    # there is no per-document state to keep them apart.
    entity_extractor = (
        RegexSteelEntityExtractor() if settings.steel_entity_extraction_enabled else None
    )

    hybrid_chunking_pipeline: ChunkingStrategy = HybridChunkingPipeline(
        structure_chunker=StructureChunker(),
        semantic_chunker=SemanticChunker(
            embedding_provider=embedding_strategy.chunking_provider,
            std_multiplier=settings.semantic_chunk_std_multiplier,
            min_sentences_for_split=settings.semantic_chunk_min_sentences,
        ),
        parent_child_chunker=parent_child_chunker,
        validator=ChunkValidator(
            min_chars=settings.chunk_validator_min_chars,
            min_ocr_confidence=settings.chunk_validator_min_ocr_confidence,
            min_ocr_confidence_drawing=settings.chunk_validator_min_ocr_confidence_drawing,
        ),
        entity_extractor=entity_extractor,
    )

    parsing_service = DocumentParsingService(
        ocr_detector=OCRDetector(min_words_per_page=settings.ocr_min_words_per_page),
        ocr_provider=get_ocr_provider(settings),
        labeled_analyzer=LabeledLayoutAnalyzer(),
        heuristic_analyzer=HeuristicLayoutAnalyzer(),
    )

    return IngestionPipeline(
        loaders=[DoclingLoader(), ImagePassthroughLoader(), UnstructuredLoader()],
        enricher=(
            DomainMetadataEnricher(
                LLMMetadataEnricher(small_llm),
                extractor=entity_extractor,
                max_chars=settings.steel_entity_max_chars,
            )
            if entity_extractor is not None
            else LLMMetadataEnricher(small_llm)
        ),
        parsing_service=parsing_service,
        chunking_strategy=hybrid_chunking_pipeline,
        embedding_strategy=embedding_strategy,
        document_repo=get_document_repository(),
        chunk_repo=get_chunk_repository(),
        vector_repo=_get_vector_repo(),
        search_repo=_get_search_repo(),
        intelligence_recorder=DocumentIntelligenceRecorder(get_intelligence_repository()),
    )
