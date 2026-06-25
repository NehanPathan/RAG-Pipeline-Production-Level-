from __future__ import annotations

import cohere
from sentence_transformers import CrossEncoder

from src.config import Settings
from src.retrieval.rerankers.base import Reranker
from src.retrieval.rerankers.bge_reranker import BGEReranker
from src.retrieval.rerankers.cohere_reranker import CohereReranker


def get_reranker(settings: Settings) -> Reranker:
    """Select and construct the configured Reranker.

    Note: constructing BGEReranker loads (and may download) the real
    cross-encoder model from HuggingFace Hub — call this once at app/DI
    startup, not per-request, and never from a unit test.
    """
    if settings.reranker_provider == "bge":
        cross_encoder = CrossEncoder(settings.bge_reranker_model)
        return BGEReranker(cross_encoder=cross_encoder, model_name=settings.bge_reranker_model)

    if settings.reranker_provider == "cohere":
        client = cohere.AsyncClientV2(api_key=settings.cohere_api_key)
        return CohereReranker(client=client, model=settings.cohere_reranker_model)

    raise ValueError(f"Unsupported reranker provider: {settings.reranker_provider!r}")
