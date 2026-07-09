"""Part 8 benchmark: Current Chunker (ParentChildOnlyStrategy) vs Hybrid
Chunker (HybridChunkingPipeline), on the same small built-in corpus and the
existing `golden_set_v1` eval dataset.

Ingests the corpus through two isolated pipelines (separate Qdrant
collections + Elasticsearch indices, same Postgres/embedding/LLM/reranker
config otherwise) so the two variants never share state, runs every
question in the dataset through each, scores with the existing
`evaluation/offline/metrics.py` functions, and writes a comparison report.

Usage:
    uv run python scripts/benchmark_chunking.py [--dataset golden_set_v1] [--keep]

`--keep` skips the cleanup step (leaves the benchmark collections/indices/
documents in place for manual inspection) -- by default everything the
script creates is deleted at the end, successful or not.

This makes real LLM API calls (enrichment, query intelligence, reranking,
answer generation, scoring) for every question against both variants --
review `src/config.py`'s active providers before running if you want to
control cost.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from src.config import get_settings  # noqa: E402
from src.domain.entities.document import Document  # noqa: E402
from src.evaluation.offline.dataset import load_dataset  # noqa: E402
from src.evaluation.offline.metrics import (  # noqa: E402
    score_answer_correctness,
    score_answer_relevancy,
    score_context_relevancy,
    score_faithfulness,
)
from src.infrastructure.database.postgres.chunk_repository import PostgresChunkRepository  # noqa: E402
from src.infrastructure.database.postgres.connection import get_session_factory  # noqa: E402
from src.infrastructure.database.postgres.document_repository import PostgresDocumentRepository  # noqa: E402
from src.infrastructure.database.redis.connection import RedisCache, get_redis_client  # noqa: E402
from src.infrastructure.search.elasticsearch.repository import (  # noqa: E402
    ElasticsearchSearchRepository,
    create_elasticsearch_client,
)
from src.infrastructure.vector_store.qdrant.cache_repository import QdrantSemanticCacheRepository  # noqa: E402
from src.infrastructure.vector_store.qdrant.repository import (  # noqa: E402
    QdrantVectorRepository,
    create_qdrant_client,
)
from src.ingestion.chunkers.chunk_validator import ChunkValidator  # noqa: E402
from src.ingestion.chunkers.chunking_strategy import ParentChildOnlyStrategy  # noqa: E402
from src.ingestion.chunkers.hybrid_chunking_pipeline import HybridChunkingPipeline  # noqa: E402
from src.ingestion.chunkers.parent_child_chunker import ChunkingConfig, ParentChildChunker  # noqa: E402
from src.ingestion.chunkers.semantic_chunker import SemanticChunker  # noqa: E402
from src.ingestion.chunkers.structure_chunker import StructureChunker  # noqa: E402
from src.ingestion.embedders.embedding_strategy import EmbeddingStrategy  # noqa: E402
from src.ingestion.embedders.openai_embedder import OpenAIEmbeddingProvider  # noqa: E402
from src.ingestion.embedders.registry import get_embedding_provider  # noqa: E402
from src.ingestion.enrichers.llm_enricher import LLMMetadataEnricher  # noqa: E402
from src.ingestion.layout.heuristic_layout_analyzer import HeuristicLayoutAnalyzer  # noqa: E402
from src.ingestion.layout.labeled_layout_analyzer import LabeledLayoutAnalyzer  # noqa: E402
from src.ingestion.loaders.docling_loader import DoclingLoader  # noqa: E402
from src.ingestion.loaders.image_loader import ImagePassthroughLoader  # noqa: E402
from src.ingestion.loaders.unstructured_loader import UnstructuredLoader  # noqa: E402
from src.ingestion.ocr.detector import OCRDetector  # noqa: E402
from src.ingestion.ocr.registry import get_ocr_provider  # noqa: E402
from src.ingestion.parsing.parsing_orchestrator import DocumentParsingService  # noqa: E402
from src.ingestion.pipeline import IngestionPipeline  # noqa: E402
from src.llm.registry import get_llm_provider  # noqa: E402
from src.retrieval.agents.filter_generator import FilterGenerator  # noqa: E402
from src.retrieval.agents.intent_classifier import IntentClassifier  # noqa: E402
from src.retrieval.agents.query_agent import QueryAgent  # noqa: E402
from src.retrieval.agents.query_expander import QueryExpander  # noqa: E402
from src.retrieval.agents.query_rewriter import QueryRewriter  # noqa: E402
from src.retrieval.agents.source_selector import SourceSelector  # noqa: E402
from src.retrieval.answer.answer_pipeline import AnswerPipeline  # noqa: E402
from src.retrieval.answer.citation_validator import CitationValidator  # noqa: E402
from src.retrieval.answer.context_assembler import ContextAssembler  # noqa: E402
from src.retrieval.answer.prompt_builder import PromptBuilder  # noqa: E402
from src.retrieval.answer.stream_generator import StreamGenerator  # noqa: E402
from src.retrieval.cache.semantic_cache import SemanticCache  # noqa: E402
from src.retrieval.context.citation_preserver import CitationPreserver  # noqa: E402
from src.retrieval.context.context_compressor import ContextCompressor  # noqa: E402
from src.retrieval.context.context_deduplicator import ContextDeduplicator  # noqa: E402
from src.retrieval.context.context_processor import ContextProcessor  # noqa: E402
from src.retrieval.context.token_budget_manager import TokenBudgetManager  # noqa: E402
from src.retrieval.context.token_counter import TokenCounter  # noqa: E402
from src.retrieval.fusers.duplicate_remover import DuplicateRemover  # noqa: E402
from src.retrieval.fusers.fuser import Fuser  # noqa: E402
from src.retrieval.fusers.rrf_fuser import RRFFusion  # noqa: E402
from src.retrieval.fusers.score_normalizer import ScoreNormalizer  # noqa: E402
from src.retrieval.hybrid_retriever import HybridRetriever  # noqa: E402
from src.retrieval.pipeline import QueryPipeline  # noqa: E402
from src.retrieval.rerankers.registry import get_reranker  # noqa: E402
from src.retrieval.searchers.bm25_searcher import BM25Searcher  # noqa: E402
from src.retrieval.searchers.vector_searcher import VectorSearcher  # noqa: E402

BENCHMARK_USER_ID = uuid.UUID("00000000-0000-0000-0000-0000000b3e5c")

# Small built-in corpus covering golden_set_v1's topics, so retrieval has
# something real to find without requiring the user to supply their own.
CORPUS = {
    "ml_failures.md": """# Machine Learning Failures in Production

## Data Drift
Data drift happens when the statistical distribution of incoming production
data diverges from the distribution the model was trained on. Left
undetected, this silently degrades model accuracy over time. Common
detection techniques include Population Stability Index (PSI), the
Kolmogorov-Smirnov test, and KL divergence between training and live
feature distributions.

## Training-Serving Skew
Training-serving skew occurs when the feature computation logic used at
training time differs from the logic used at inference time -- for
example, a feature engineered in a batch Spark job that isn't reproduced
exactly in the low-latency serving path.

## Monitoring and Feedback Loops
Production ML systems fail quietly without monitoring: prediction
distribution shifts, latency regressions, and silent feature pipeline
breakages all require dedicated observability. Feedback loops -- capturing
real user outcomes and feeding them back into retraining -- close the gap
between offline validation and real-world performance.

## Infrastructure Underestimation
Teams frequently underestimate the infrastructure needed for reliable
serving: autoscaling for traffic spikes, redundant model replicas, and
graceful degradation when a downstream feature store is unavailable.
""",
    "rag_evaluation.md": """# Evaluating RAG Systems

## Offline vs Online Evaluation
Offline evaluation runs a fixed golden dataset of questions with known
ground-truth answers through the pipeline, scoring faithfulness, answer
relevancy, and context relevancy -- typically using an LLM-as-judge.
Online evaluation instead measures real production signals: user thumbs
up/down feedback, click-through on citations, and end-to-end latency.

## Key Metrics
- **Faithfulness**: does the generated answer only use information present
  in the retrieved context, without hallucinating facts?
- **Answer Relevancy**: does the answer actually address the question asked?
- **Context Relevancy**: are the retrieved chunks actually relevant to the
  question, independent of the generated answer?
- **Answer Correctness**: does the answer match a known ground-truth answer?

## Chunking's Effect on Evaluation
Chunk boundaries directly affect context relevancy: a chunk that splits a
sentence across a heading boundary, or merges two unrelated topics into one
chunk, will retrieve as "relevant" by keyword overlap while actually
providing poor context for the generator.
""",
    "hybrid_retrieval.md": """# Hybrid Retrieval and Chunking Strategy

## Reciprocal Rank Fusion
Combining a vector similarity search with a BM25 keyword search, then
merging the two ranked lists with Reciprocal Rank Fusion (RRF), consistently
outperforms either search method alone -- vector search catches semantic
matches with no keyword overlap, while BM25 catches exact terminology
(product names, error codes) that embeddings sometimes blur.

## Cross-Encoder Reranking
A cross-encoder reranker reads the query and each candidate chunk together,
producing a much more precise relevance score than the bi-encoder used for
the initial vector search -- at the cost of being too slow to run over the
full corpus, so it's applied only to the top candidates after fusion.

## Chunking Best Practices
Good chunking respects document structure -- headings, paragraphs, tables,
and lists -- rather than splitting purely on a fixed token count. Parent
chunks provide full context for the generator, while smaller child chunks
give the retriever a precise target. Semantic-boundary detection, adaptive
similarity thresholds, and chunk-quality validation (rejecting
whitespace-only, duplicate, or garbled chunks) further improve retrieval
quality over naive fixed-size splitting.
""",
}


@dataclass
class VariantMetrics:
    name: str
    chunk_count: int = 0
    indexing_time_ms: float = 0.0
    embedding_cost_estimate_usd: float = 0.0
    faithfulness: float = 0.0
    answer_relevancy: float = 0.0
    context_relevancy: float = 0.0
    answer_correctness: float = 0.0
    citation_count_avg: float = 0.0
    avg_query_latency_ms: float = 0.0
    sample_size: int = 0


@dataclass
class BenchmarkReport:
    dataset: str
    current: VariantMetrics = field(default_factory=lambda: VariantMetrics(name="current"))
    hybrid: VariantMetrics = field(default_factory=lambda: VariantMetrics(name="hybrid"))


# OpenAI text-embedding-3-large list price, per 1M tokens, as of writing --
# used only for a rough order-of-magnitude cost estimate, not a live lookup.
_OPENAI_EMBEDDING_USD_PER_1M_TOKENS = 0.13


def _build_ingestion_pipeline(
    chunking_strategy,
    vector_repo: QdrantVectorRepository,
    search_repo: ElasticsearchSearchRepository,
    embedding_strategy: EmbeddingStrategy,
) -> IngestionPipeline:
    settings = get_settings()
    small_llm = get_llm_provider(settings, role="small")
    return IngestionPipeline(
        loaders=[DoclingLoader(), ImagePassthroughLoader(), UnstructuredLoader()],
        enricher=LLMMetadataEnricher(small_llm),
        parsing_service=DocumentParsingService(
            ocr_detector=OCRDetector(min_words_per_page=settings.ocr_min_words_per_page),
            ocr_provider=get_ocr_provider(settings),
            labeled_analyzer=LabeledLayoutAnalyzer(),
            heuristic_analyzer=HeuristicLayoutAnalyzer(),
        ),
        chunking_strategy=chunking_strategy,
        embedding_strategy=embedding_strategy,
        document_repo=PostgresDocumentRepository(get_session_factory()),
        chunk_repo=PostgresChunkRepository(get_session_factory()),
        vector_repo=vector_repo,
        search_repo=search_repo,
    )


def _build_query_pipeline(
    vector_repo: QdrantVectorRepository,
    search_repo: ElasticsearchSearchRepository,
    cache_repo: QdrantSemanticCacheRepository,
    embedder,
) -> QueryPipeline:
    settings = get_settings()
    small_llm = get_llm_provider(settings, role="small")
    large_llm = get_llm_provider(settings, role="large")

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
    fuser = Fuser(rrf=RRFFusion(k=settings.rrf_k), normalizer=ScoreNormalizer(), deduplicator=DuplicateRemover())
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
        repository=cache_repo, embedding_provider=embedder, score_threshold=settings.semantic_cache_score_threshold
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


async def _ingest_corpus(pipeline: IngestionPipeline, tmp_dir: Path) -> tuple[list[uuid.UUID], int, float]:
    document_ids: list[uuid.UUID] = []
    total_chunks = 0
    start = time.perf_counter()

    for file_name, content in CORPUS.items():
        file_path = tmp_dir / file_name
        file_path.write_text(content, encoding="utf-8")
        document = Document(
            file_name=file_name,
            file_type="md",
            file_size_bytes=len(content.encode("utf-8")),
            user_id=BENCHMARK_USER_ID,
            file_path=str(file_path),
        )
        result = await pipeline.ingest(document, file_path)
        if result.status.value != "indexed":
            print(f"  [warn] {file_name} failed to index: {result.error}")
            continue
        document_ids.append(document.id)
        total_chunks += result.chunks_created

    elapsed_ms = (time.perf_counter() - start) * 1000
    return document_ids, total_chunks, elapsed_ms


async def _run_variant(
    name: str,
    chunking_strategy,
    dataset,
    tmp_dir: Path,
    collection_suffix: str,
    keep: bool = False,
) -> VariantMetrics:
    settings = get_settings()
    metrics = VariantMetrics(name=name)

    qdrant_client = create_qdrant_client()
    es_client = create_elasticsearch_client()
    vector_repo = QdrantVectorRepository(
        client=qdrant_client, collection_name=f"{settings.qdrant_collection_name}_bench_{collection_suffix}"
    )
    search_repo = ElasticsearchSearchRepository(
        client=es_client, index_name=f"{settings.elasticsearch_index_name}_bench_{collection_suffix}"
    )
    cache_repo = QdrantSemanticCacheRepository(
        client=qdrant_client, collection_name=f"{settings.qdrant_cache_collection_name}_bench_{collection_suffix}"
    )
    embedder = OpenAIEmbeddingProvider(
        api_key=settings.openai_api_key,
        model=settings.openai_embedding_model,
        dimensions=settings.openai_embedding_dimensions,
        batch_size=settings.embedding_batch_size,
        cache=RedisCache(get_redis_client()),
        cache_ttl=settings.redis_ttl_embedding,
    )
    embedding_strategy = EmbeddingStrategy(
        chunking_provider=get_embedding_provider(settings, role="chunking"), retrieval_provider=embedder
    )

    ingestion_pipeline = _build_ingestion_pipeline(chunking_strategy, vector_repo, search_repo, embedding_strategy)
    document_ids, total_chunks, indexing_ms = await _ingest_corpus(ingestion_pipeline, tmp_dir)
    metrics.chunk_count = total_chunks
    metrics.indexing_time_ms = indexing_ms

    chunk_repo = PostgresChunkRepository(get_session_factory())
    total_tokens = 0
    for doc_id in document_ids:
        chunks = await chunk_repo.get_by_document(doc_id)
        total_tokens += sum(c.token_count for c in chunks if c.has_embedding())
    metrics.embedding_cost_estimate_usd = (total_tokens / 1_000_000) * _OPENAI_EMBEDDING_USD_PER_1M_TOKENS

    query_pipeline = _build_query_pipeline(vector_repo, search_repo, cache_repo, embedder)
    scorer_llm = get_llm_provider(settings, role="small")

    scores: dict[str, list[float]] = {"faithfulness": [], "answer_relevancy": [], "context_relevancy": [], "answer_correctness": []}
    citation_counts: list[int] = []
    latencies_ms: list[float] = []

    for sample in dataset.samples:
        start = time.perf_counter()
        inspection = await query_pipeline.inspect(sample.question, user_id=BENCHMARK_USER_ID)
        contexts = [r.chunk.content for r in inspection.reranked_results[:5]]

        answer = ""
        citations: list = []
        async for event in query_pipeline.answer(sample.question, user_id=BENCHMARK_USER_ID):
            if event["type"] == "done":
                answer = event["answer"]
                citations = event.get("citations", [])
                break
            if event["type"] == "error":
                break
        latencies_ms.append((time.perf_counter() - start) * 1000)
        citation_counts.append(len(citations))

        if not answer or not contexts:
            continue

        scores["faithfulness"].append(await score_faithfulness(scorer_llm, answer, contexts))
        scores["answer_relevancy"].append(await score_answer_relevancy(scorer_llm, sample.question, answer))
        scores["context_relevancy"].append(await score_context_relevancy(scorer_llm, sample.question, contexts))
        if sample.ground_truth:
            scores["answer_correctness"].append(
                await score_answer_correctness(scorer_llm, sample.question, answer, sample.ground_truth)
            )

    metrics.faithfulness = _avg(scores["faithfulness"])
    metrics.answer_relevancy = _avg(scores["answer_relevancy"])
    metrics.context_relevancy = _avg(scores["context_relevancy"])
    metrics.answer_correctness = _avg(scores["answer_correctness"])
    metrics.citation_count_avg = _avg([float(c) for c in citation_counts])
    metrics.avg_query_latency_ms = _avg(latencies_ms)
    metrics.sample_size = len(scores["faithfulness"])

    # Cleanup this variant's isolated stores + documents (best-effort),
    # unless --keep was passed for manual inspection.
    if not keep:
        document_repo = PostgresDocumentRepository(get_session_factory())
        for doc_id in document_ids:
            try:
                await chunk_repo.delete_by_document(doc_id)
                await document_repo.delete(doc_id)
            except Exception as e:
                print(f"  [warn] cleanup failed for {doc_id}: {e}")
        try:
            qdrant_client.delete_collection(f"{settings.qdrant_collection_name}_bench_{collection_suffix}")
            qdrant_client.delete_collection(f"{settings.qdrant_cache_collection_name}_bench_{collection_suffix}")
        except Exception:
            pass
        try:
            await es_client.indices.delete(index=f"{settings.elasticsearch_index_name}_bench_{collection_suffix}")
        except Exception:
            pass

    return metrics


def _avg(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _print_report(report: BenchmarkReport) -> None:
    c, h = report.current, report.hybrid
    rows = [
        ("chunk_count", c.chunk_count, h.chunk_count),
        ("indexing_time_ms", f"{c.indexing_time_ms:.0f}", f"{h.indexing_time_ms:.0f}"),
        ("embedding_cost_estimate_usd", f"{c.embedding_cost_estimate_usd:.5f}", f"{h.embedding_cost_estimate_usd:.5f}"),
        ("faithfulness", f"{c.faithfulness:.2f}", f"{h.faithfulness:.2f}"),
        ("answer_relevancy", f"{c.answer_relevancy:.2f}", f"{h.answer_relevancy:.2f}"),
        ("context_relevancy", f"{c.context_relevancy:.2f}", f"{h.context_relevancy:.2f}"),
        ("answer_correctness", f"{c.answer_correctness:.2f}", f"{h.answer_correctness:.2f}"),
        ("citation_count_avg", f"{c.citation_count_avg:.1f}", f"{h.citation_count_avg:.1f}"),
        ("avg_query_latency_ms", f"{c.avg_query_latency_ms:.0f}", f"{h.avg_query_latency_ms:.0f}"),
        ("sample_size", c.sample_size, h.sample_size),
    ]
    print(f"\n{'Metric':<30}{'Current (ParentChild)':<25}{'Hybrid':<25}")
    print("-" * 80)
    for label, cv, hv in rows:
        print(f"{label:<30}{str(cv):<25}{str(hv):<25}")
    print(
        "\nNote: context_relevancy is an LLM-judge proxy for retrieval "
        "precision/recall -- this corpus has no hand-labeled relevant-chunk "
        "annotations, so true recall/precision against a gold relevance set "
        "isn't computed here."
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default="golden_set_v1")
    parser.add_argument("--keep", action="store_true", help="skip cleanup of benchmark stores/documents")
    args = parser.parse_args()

    dataset = load_dataset(args.dataset)
    print(f"Loaded {len(dataset)} questions from '{args.dataset}'")

    settings = get_settings()

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        print("\n=== Running CURRENT chunker (ParentChildOnlyStrategy) ===")
        current_strategy = ParentChildOnlyStrategy(
            ParentChildChunker(
                ChunkingConfig(
                    parent_chunk_size=settings.parent_chunk_size,
                    child_chunk_size=settings.child_chunk_size,
                    overlap=settings.chunk_overlap,
                )
            )
        )
        current_metrics = await _run_variant(
            "current", current_strategy, dataset, tmp_dir, "current", keep=args.keep
        )

        print("\n=== Running HYBRID chunker (HybridChunkingPipeline) ===")
        hybrid_strategy = HybridChunkingPipeline(
            structure_chunker=StructureChunker(),
            semantic_chunker=SemanticChunker(
                embedding_provider=get_embedding_provider(settings, role="chunking"),
                std_multiplier=settings.semantic_chunk_std_multiplier,
                min_sentences_for_split=settings.semantic_chunk_min_sentences,
            ),
            parent_child_chunker=ParentChildChunker(
                ChunkingConfig(
                    parent_chunk_size=settings.parent_chunk_size,
                    child_chunk_size=settings.child_chunk_size,
                    overlap=settings.chunk_overlap,
                )
            ),
            validator=ChunkValidator(
                min_chars=settings.chunk_validator_min_chars,
                min_ocr_confidence=settings.chunk_validator_min_ocr_confidence,
            ),
        )
        hybrid_metrics = await _run_variant(
            "hybrid", hybrid_strategy, dataset, tmp_dir, "hybrid", keep=args.keep
        )

    report = BenchmarkReport(dataset=args.dataset, current=current_metrics, hybrid=hybrid_metrics)
    _print_report(report)

    report_dir = ROOT / "data" / "eval_datasets" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"chunking_benchmark_{int(time.time())}.json"
    report_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
    print(f"\nReport written to {report_path}")


if __name__ == "__main__":
    asyncio.run(main())
