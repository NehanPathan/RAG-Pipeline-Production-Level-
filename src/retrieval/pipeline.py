from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from src.domain.repositories.search_repository import BM25ScoredChunk
from src.domain.repositories.vector_repository import ScoredChunk
from src.domain.value_objects.processed_query import ProcessedQuery
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.domain.value_objects.retrieval_trace import RetrievalTrace
from src.retrieval.agents.query_agent import QueryAgent
from src.retrieval.answer.answer_pipeline import AnswerPipeline
from src.retrieval.cache.semantic_cache import SemanticCache
from src.retrieval.context.context_processor import ContextProcessor
from src.retrieval.fusers.fuser import Fuser
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.rerankers.base import Reranker


@dataclass
class RetrievalInspection:
    """Full intermediate state from running a query through Modules A-D --
    the /retrieval/inspect debug payload. Deliberately stops short of
    Module E/G: the documented /retrieval/inspect contract
    (07_api_design.md) never includes a generated answer.
    """

    processed_query: ProcessedQuery
    vector_results: list[ScoredChunk]
    bm25_results: list[BM25ScoredChunk]
    fused_results: list[FusedChunk]
    reranked_results: list[RerankedChunk]
    trace: RetrievalTrace = field(default_factory=RetrievalTrace)


class QueryPipeline:
    """Orchestrates Modules A-G end to end.

    Two entry points sharing the same A->D retrieval core:
    - inspect(): A -> B -> C -> D only, for /retrieval/inspect.
    - answer(): semantic cache check -> A -> B -> C -> D -> E -> G,
      yielding SSE-ready event dicts, then stores the result in cache.
    """

    def __init__(
        self,
        query_agent: QueryAgent,
        hybrid_retriever: HybridRetriever,
        fuser: Fuser,
        reranker: Reranker,
        context_processor: ContextProcessor,
        answer_pipeline: AnswerPipeline,
        semantic_cache: SemanticCache,
        vector_top_k: int,
        bm25_top_k: int,
        rerank_top_n: int,
    ) -> None:
        self._query_agent = query_agent
        self._hybrid_retriever = hybrid_retriever
        self._fuser = fuser
        self._reranker = reranker
        self._context_processor = context_processor
        self._answer_pipeline = answer_pipeline
        self._semantic_cache = semantic_cache
        self._vector_top_k = vector_top_k
        self._bm25_top_k = bm25_top_k
        self._rerank_top_n = rerank_top_n

    async def inspect(self, query: str, user_id: uuid.UUID | None = None) -> RetrievalInspection:
        return await self._retrieve(query, user_id)

    async def answer(self, query: str, user_id: uuid.UUID | None = None) -> AsyncIterator[dict]:
        cached = await self._semantic_cache.lookup(query)
        if cached is not None:
            yield {
                "type": "done",
                "answer": cached.answer,
                "citations": cached.citations,
                "cached": True,
            }
            return

        inspection = await self._retrieve(query, user_id)
        compressed_chunks, citations = await self._context_processor.process(
            inspection.processed_query.rewritten_query, inspection.reranked_results
        )

        async for event in self._answer_pipeline.generate(
            query=inspection.processed_query.rewritten_query,
            chunks=compressed_chunks,
            citations=citations,
        ):
            yield event
            if event["type"] == "done":
                await self._semantic_cache.store(
                    query,
                    event["answer"],
                    event["citations"],
                    model_used=event.get("model_used", ""),
                )

    async def _retrieve(self, query: str, user_id: uuid.UUID | None) -> RetrievalInspection:
        start = time.perf_counter()
        processed = await self._query_agent.process(query, user_id=user_id)
        query_processing_ms = int((time.perf_counter() - start) * 1000)

        vector_results, bm25_results, trace = await self._hybrid_retriever.retrieve(
            queries=processed.all_queries,
            vector_top_k=self._vector_top_k,
            bm25_top_k=self._bm25_top_k,
            vector_filter=processed.filters.to_vector_filter(),
            bm25_filter=processed.filters.to_bm25_filter(),
        )
        trace.query_processing_ms = query_processing_ms

        fusion_start = time.perf_counter()
        fused = await self._fuser.fuse(vector_results, bm25_results)
        trace.fusion_ms = int((time.perf_counter() - fusion_start) * 1000)
        trace.fused_count = len(fused)

        rerank_start = time.perf_counter()
        reranked = await self._reranker.rerank(
            processed.rewritten_query, fused, top_n=self._rerank_top_n
        )
        trace.reranking_ms = int((time.perf_counter() - rerank_start) * 1000)
        trace.reranked_count = len(reranked)
        trace.total_ms = int((time.perf_counter() - start) * 1000)

        return RetrievalInspection(
            processed_query=processed,
            vector_results=vector_results,
            bm25_results=bm25_results,
            fused_results=fused,
            reranked_results=reranked,
            trace=trace,
        )
