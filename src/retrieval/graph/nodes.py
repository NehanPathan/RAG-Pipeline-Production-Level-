"""Graph nodes for the query pipeline.

Each node is a thin function over the components `QueryPipeline` already
owns. That split is deliberate: the graph owns *control flow* (which was
previously a nest of early returns and delegating async generators spread
across five methods), and the pipeline continues to own *components* and the
governance helpers. Nothing about how a chunk is retrieved, compressed,
redacted or checked changed here -- only how the steps are sequenced.

Nodes emit SSE-shaped events through LangGraph's custom stream writer, so
the event contract the chat route and the UI consume is unchanged.
"""

from __future__ import annotations

import time
from typing import Any

from langgraph.config import get_stream_writer

from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.governance.runtime_flags import get_flags
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import (
    answers_total,
    citation_validation,
    query_latency,
    semantic_cache_lookups,
)
from src.retrieval.graph.state import QueryState
from src.routing.routes import Route, RouteDecision, RouteSource

logger = get_logger(__name__)


class QueryNodes:
    """Nodes bound to one pipeline instance.

    Held as a class rather than closures purely so each node is separately
    addressable in tests and readable in a stack trace.
    """

    def __init__(self, pipeline: Any) -> None:
        self._p = pipeline

    # -- Module 0: kill switch -------------------------------------------

    async def check_kill_switch(self, state: QueryState) -> dict[str, Any]:
        """MANAGE: an operator can stop answering without a redeploy."""
        writer = get_stream_writer()
        flags = await get_flags()
        if flags.answering_enabled:
            return {"flags": flags}

        # Returned as a normal `done` event rather than an error, so clients
        # render it as a message instead of a failed request.
        answers_total.labels(outcome="disabled").inc()
        logger.warning("answering_disabled_by_kill_switch", trace_id=state["trace_id"])
        writer(
            {
                "type": "done",
                "answer": "Answering is temporarily disabled by an administrator.",
                "citations": [],
                "cached": False,
                "trace_id": state["trace_id"],
                "refused": True,
                "refusal_reason": "kill_switch",
            }
        )
        return {"flags": flags, "finished": True}

    # -- Semantic cache ---------------------------------------------------

    async def lookup_cache(self, state: QueryState) -> dict[str, Any]:
        writer = get_stream_writer()
        if not state["flags"].semantic_cache_enabled:
            semantic_cache_lookups.labels(result="disabled").inc()
            return {}

        cached = await self._p._semantic_cache.lookup(state["query"])
        semantic_cache_lookups.labels(result="hit" if cached else "miss").inc()
        if cached is None:
            return {}

        answers_total.labels(outcome="cached").inc()
        query_latency.labels(intent="cached", provider="cache").observe(
            time.perf_counter() - state["started_at"]
        )
        writer(
            {
                "type": "done",
                "answer": cached.answer,
                "citations": cached.citations,
                "cached": True,
                "trace_id": state["trace_id"],
                "route": "cached",
                "route_source": "cache",
                # A cached answer is grounded only if it carried citations
                # when it was stored -- a cached tool answer did not.
                "grounded": bool(cached.citations),
                "refused": False,
                "context_texts": [],
            }
        )
        return {"finished": True}

    # -- Routing ----------------------------------------------------------

    async def route_query(self, state: QueryState) -> dict[str, Any]:
        """Decide how to answer before paying to answer.

        Routes that need no documents never touch embedding, vector search,
        BM25, fusion, reranking or compression.
        """
        if self._p._router is None:
            return {
                "decision": RouteDecision(
                    route=Route.RAG, source=RouteSource.FORCED, reason="no router configured"
                )
            }
        return {"decision": await self._p._router.route(state["query"])}

    # -- Off-pipeline answers (greeting / refuse / tool / model knowledge) --

    async def answer_off_pipeline(self, state: QueryState) -> dict[str, Any]:
        """Answer a query the router sent somewhere other than RAG.

        Every branch emits the same `done` event shape the RAG path does, so
        the chat route, the UI and the online evaluator need no special
        casing for routed answers.
        """
        writer = get_stream_writer()
        decision: RouteDecision = state["decision"]
        route = decision.route
        trace_id = state["trace_id"]
        started = state["started_at"]

        if route is Route.REFUSE:
            answers_total.labels(outcome="refused").inc()
            writer(
                self._p._done(
                    "I need an actual question to answer.", decision, trace_id, refused=True
                )
            )
            return {"finished": True}

        if route is Route.GREETING:
            # Zero model calls, zero retrieval. The cheapest possible answer
            # to the most common non-question in real traffic.
            answers_total.labels(outcome="served").inc()
            query_latency.labels(intent="greeting", provider="none").observe(
                time.perf_counter() - started
            )
            writer(self._p._done(str(decision.args.get("reply") or "Hello."), decision, trace_id))
            return {"finished": True}

        if route is Route.LLM_KNOWLEDGE:
            return await self._answer_from_model(state)

        tool_name = route.tool_name
        if not tool_name:
            logger.error("route_has_no_handler", route=route.value)
            answers_total.labels(outcome="error").inc()
            writer(
                {
                    "type": "error",
                    "code": "unroutable",
                    "message": f"No handler for route {route.value}.",
                    "trace_id": trace_id,
                }
            )
            return {"finished": True}

        result = await self._p._run_tool(
            tool_name, state["query"], decision, state["principal"], trace_id
        )
        if not result.succeeded:
            # The router's guess was that a tool could answer this. Being
            # wrong should cost latency, not an answer.
            logger.info("tool_failed_falling_back_to_rag", tool=tool_name, error=result.error)
            return {"fallback_to_rag": True}

        answer = self._p._redact_answer(result.answer)
        answers_total.labels(outcome="served").inc()
        query_latency.labels(intent=route.value, provider=tool_name).observe(
            time.perf_counter() - started
        )
        if result.cacheable and state["flags"].semantic_cache_enabled:
            await self._p._semantic_cache.store(
                state["query"], answer, result.citations, model_used=tool_name
            )
        writer(self._p._done(answer, decision, trace_id, citations=result.citations))
        return {"finished": True}

    async def _answer_from_model(self, state: QueryState) -> dict[str, Any]:
        """General knowledge: one model call, no retrieval, no citations.

        The answer is explicitly marked as not document-sourced. Presenting
        model knowledge with the same authority as a cited passage is exactly
        the confusion a RAG system exists to prevent, so the distinction is
        carried in the event rather than left to the reader.
        """
        writer = get_stream_writer()
        if self._p._direct_llm is None:
            logger.warning("direct_llm_unavailable_falling_back")
            return {"fallback_to_rag": True}

        tokens: list[str] = []
        try:
            async for token in self._p._direct_llm.stream(
                self._p._general_knowledge_prompt(state["query"]), max_tokens=800, temperature=0.3
            ):
                tokens.append(token)
                writer({"type": "token", "content": token})
        except Exception as exc:
            answers_total.labels(outcome="error").inc()
            logger.error("direct_llm_failed", error=str(exc))
            writer(
                {
                    "type": "error",
                    "code": "generation_failed",
                    "message": str(exc),
                    "trace_id": state["trace_id"],
                }
            )
            return {"finished": True}

        answer = self._p._redact_answer("".join(tokens))
        answers_total.labels(outcome="served").inc()
        query_latency.labels(intent="llm_knowledge", provider=self._p._direct_llm.model_id).observe(
            time.perf_counter() - state["started_at"]
        )
        writer(
            self._p._done(
                answer,
                state["decision"],
                state["trace_id"],
                model_used=self._p._direct_llm.model_id,
            )
        )
        return {"finished": True}

    # -- Modules A-D: retrieval -------------------------------------------

    async def retrieve(self, state: QueryState) -> dict[str, Any]:
        inspection = await self._p._retrieve(
            state["query"],
            state.get("user_id"),
            state["principal"],
            history=state.get("history"),
        )
        return {"inspection": inspection}

    # -- Module E: context, redaction, pre-generation grounding check -----

    async def build_context(self, state: QueryState) -> dict[str, Any]:
        writer = get_stream_writer()
        inspection = state["inspection"]
        compressed_chunks, citations = await self._p._context_processor.process(
            inspection.processed_query.rewritten_query, inspection.reranked_results
        )

        # MAP: scrub personal data from retrieved passages *before* they enter
        # the prompt. Once a passage is in the prompt it has left the
        # deployment, so redacting the answer alone would be too late.
        compressed_chunks = self._p._redact_context(compressed_chunks)

        # GOVERN (C-GOV-03): nothing retrieved means the model has no grounds
        # to answer from. Checked *before* generation so an ungrounded answer
        # is never produced -- and never billed for -- rather than generated
        # and then suppressed.
        decision = self._p._policy.check_grounding(
            context_chunks=len(compressed_chunks), valid_citations=0
        )
        if decision.denied and decision.control_id == "C-GOV-03":
            async for event in self._p._refuse(
                decision,
                state["trace_id"],
                inspection.processed_query.intent.type.value,
                self._p._answer_pipeline.model_id,
                state["started_at"],
            ):
                writer(event)
            return {"finished": True}

        return {"compressed_chunks": compressed_chunks, "citations": citations}

    # -- Module G: generation and post-generation grounding check ---------

    async def generate(self, state: QueryState) -> dict[str, Any]:
        writer = get_stream_writer()
        inspection = state["inspection"]
        compressed_chunks = state["compressed_chunks"]
        intent = inspection.processed_query.intent.type.value
        provider = self._p._answer_pipeline.model_id
        trace_id = state["trace_id"]

        answer_text = ""
        citation_payload: list[dict[str, Any]] = []

        async for event in self._p._answer_pipeline.generate(
            query=inspection.processed_query.rewritten_query,
            chunks=compressed_chunks,
            citations=state["citations"],
        ):
            if event["type"] == "error":
                answers_total.labels(outcome="error").inc()
                event["trace_id"] = trace_id
                writer(event)
                return {"finished": True}

            if event["type"] != "done":
                writer(event)
                continue

            answer_text = event["answer"]
            citation_payload = event["citations"]

            # GOVERN (C-GOV-04): the answer exists but cites nothing that was
            # retrieved. CitationValidator has already discarded fabricated
            # markers, so an empty list here means the model either invented
            # every citation or ignored the context entirely.
            post_decision = self._p._policy.check_grounding(
                context_chunks=len(compressed_chunks),
                valid_citations=len(citation_payload),
            )
            if post_decision.denied:
                citation_validation.labels(result="uncited_answer").inc()
                if self._p._policy.enforcing:
                    async for refusal in self._p._refuse(
                        post_decision, trace_id, intent, provider, state["started_at"]
                    ):
                        writer(refusal)
                    return {"finished": True}
                # MONITOR mode: the violation is counted and audited, but the
                # answer still ships. This is the intended rollout path for a
                # new control -- watch the counter before enforcing.
                await audit_record(
                    action=AuditAction.POLICY_VIOLATION,
                    actor_id=state["principal"].user_id,
                    actor_role=state["principal"].role,
                    resource_type="answer",
                    outcome=AuditOutcome.ALLOWED,
                    reason=post_decision.reason,
                    control_id=post_decision.control_id,
                )
            else:
                citation_validation.labels(result="valid").inc()

            # Second redaction pass: catches personal data the model
            # reconstructed or carried in from conversation history, which
            # context redaction cannot reach.
            event["answer"] = answer_text = self._p._redact_answer(answer_text)
            event["trace_id"] = trace_id
            event["refused"] = False
            event["route"] = Route.RAG.value
            event["grounded"] = True
            # The redacted question, so the caller persists what was actually
            # sent rather than the raw text it received.
            event["question"] = state["query"]
            # Internal-only key: the exact text the model was grounded on.
            # The online judge needs it to score faithfulness, and the message
            # row needs it for the audit trail. The chat route pops it before
            # serialising, so it never inflates the SSE payload.
            event["context_texts"] = [c.compressed_content for c in compressed_chunks]
            writer(event)

            answers_total.labels(outcome="served").inc()
            if state["flags"].semantic_cache_enabled:
                # Cached under the *resolved* query, not the raw one.
                #
                # The cache is keyed on query similarity and has no notion of
                # a conversation, so a follow-up cached under its own words
                # is served to every other conversation that phrases one the
                # same way. "What about the bolts?" asked about one drawing
                # came back verbatim for a different drawing in a different
                # conversation -- the answer confident, cited, and about the
                # wrong sheet.
                #
                # Storing the resolved form fixes it without a second cache
                # key: an elliptical question resolves to something naming
                # its own subject, which no other conversation's raw text
                # matches, while a self-contained question resolves to
                # roughly itself and still caches normally.
                await self._p._semantic_cache.store(
                    inspection.processed_query.rewritten_query or state["query"],
                    answer_text,
                    citation_payload,
                    model_used=event.get("model_used", ""),
                )

        query_latency.labels(intent=intent, provider=provider).observe(
            time.perf_counter() - state["started_at"]
        )
        return {"answer": answer_text, "citation_payload": citation_payload, "finished": True}
