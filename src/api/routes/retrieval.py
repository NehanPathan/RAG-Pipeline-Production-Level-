from __future__ import annotations

import uuid

from fastapi import APIRouter
from pydantic import BaseModel

from src.api.dependencies import get_query_pipeline
from src.application.use_cases.inspect_retrieval import InspectRetrievalUseCase
from src.monitoring.logger import get_logger
from src.retrieval.pipeline import RetrievalInspection

router = APIRouter()
logger = get_logger(__name__)


class RetrievalInspectRequest(BaseModel):
    query: str
    user_id: str | None = None


@router.post("/retrieval/inspect")
async def inspect_retrieval(request: RetrievalInspectRequest) -> dict:
    """Full debug retrieval run -- Modules A-D only, no answer generation.
    Matches the payload shape documented in 07_api_design.md.
    """
    logger.info("retrieval_inspect_request", query=request.query[:100])

    use_case = InspectRetrievalUseCase(pipeline=get_query_pipeline())
    user_id = uuid.UUID(request.user_id) if request.user_id else None

    result = await use_case.execute(request.query, user_id=user_id)
    return _to_response(result)


def _to_response(result: RetrievalInspection) -> dict:
    return {
        "original_query": result.processed_query.original_query,
        "processed_query": {
            "rewritten": result.processed_query.rewritten_query,
            "expanded": result.processed_query.expanded_queries,
            "intent": result.processed_query.intent.type.value,
            "domain": result.processed_query.intent.domain,
            "selected_sources": result.processed_query.selected_sources,
        },
        "vector_results": [
            {
                "chunk_id": str(item.chunk.id),
                "content": item.chunk.content,
                "document_name": item.chunk.document_name,
                "page_number": item.chunk.chunk_metadata.page_number,
                "vector_score": item.score,
                "rank": item.rank,
            }
            for item in result.vector_results
        ],
        "bm25_results": [
            {
                "chunk_id": str(item.chunk.id),
                "content": item.chunk.content,
                "document_name": item.chunk.document_name,
                "page_number": item.chunk.chunk_metadata.page_number,
                "bm25_score": item.bm25_score,
                "rank": item.rank,
            }
            for item in result.bm25_results
        ],
        "fused_results": [
            {
                "chunk_id": str(item.chunk.id),
                "vector_score": item.vector_score,
                "bm25_score": item.bm25_score,
                "rrf_score": item.rrf_score,
                "rank": item.rank,
            }
            for item in result.fused_results
        ],
        "reranked_results": [
            {
                "chunk_id": str(item.chunk.id),
                "rerank_score": item.rerank_score,
                "final_rank": item.final_rank,
            }
            for item in result.reranked_results
        ],
        "latency_breakdown": {
            "query_processing_ms": result.trace.query_processing_ms,
            "vector_search_ms": result.trace.vector_search_ms,
            "bm25_search_ms": result.trace.bm25_search_ms,
            "fusion_ms": result.trace.fusion_ms,
            "reranking_ms": result.trace.reranking_ms,
            "total_ms": result.trace.total_ms,
        },
    }
