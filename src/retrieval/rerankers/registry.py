from __future__ import annotations

import cohere
from sentence_transformers import CrossEncoder

from src.config import Settings
from src.retrieval.rerankers.base import Reranker
from src.retrieval.rerankers.bge_reranker import BGEReranker
from src.retrieval.rerankers.cohere_reranker import CohereReranker
from src.retrieval.rerankers.passthrough_reranker import PassthroughReranker


def get_reranker(settings: Settings) -> Reranker:
    """Select and construct the configured Reranker.

    - "passthrough": no ML inference, returns RRF-sorted top_n (best for CPU-only)
    - "bge": local cross-encoder via sentence-transformers (needs GPU or is very slow on CPU)
    - "cohere": cloud reranker API (requires COHERE_API_KEY)
    """
    if settings.reranker_provider == "passthrough":
        return PassthroughReranker()

    if settings.reranker_provider == "bge":
        cross_encoder = CrossEncoder(settings.bge_reranker_model)
        return BGEReranker(cross_encoder=cross_encoder, model_name=settings.bge_reranker_model)

    if settings.reranker_provider == "cohere":
        client = cohere.AsyncClientV2(api_key=settings.cohere_api_key)
        return CohereReranker(client=client, model=settings.cohere_reranker_model)

    raise ValueError(f"Unsupported reranker provider: {settings.reranker_provider!r}")
