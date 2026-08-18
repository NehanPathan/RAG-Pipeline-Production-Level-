"""Intelligent query router — decide *how* to answer before paying to answer.

Before this existed, every query took the same path: embed, vector search,
BM25 search, fuse, rerank, compress, generate. "hi" cost the same as "what is
our refund policy for enterprise customers in the EU". That is the single
largest source of avoidable cost and latency in a RAG system, because the
majority of real traffic does not need retrieval at all.

The router classifies first and dispatches second:

    greeting / smalltalk  -> canned reply, zero model calls
    arithmetic            -> calculator tool, zero model calls
    date & time           -> clock, zero model calls
    translation           -> one small-model call, no retrieval
    general knowledge     -> one model call, no retrieval
    web-current facts     -> web search (opt-in)
    structured data       -> read-only SQL (opt-in)
    document questions    -> the full hybrid RAG pipeline

Two design decisions worth stating:

**Rules run before the LLM.** A deterministic rule that matches is free,
instant and reproducible. The classifier call only happens for queries no
rule recognises, which in practice is the interesting minority.

**Ambiguity resolves to RAG.** Below `router_min_confidence` the router
chooses full retrieval. Answering from the wrong source is a correctness
failure; an unnecessary retrieval is only a cost. Those are not equivalent,
so the fallback is deliberately the expensive-but-safe direction.
"""

from src.routing.router import QueryRouter
from src.routing.routes import Route, RouteDecision, RouteSource

__all__ = ["QueryRouter", "Route", "RouteDecision", "RouteSource"]
