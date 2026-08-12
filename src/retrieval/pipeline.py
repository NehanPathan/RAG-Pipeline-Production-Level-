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
from src.domain.value_objects.context_bundle import CompressedChunk
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.governance.pii import get_question_redactor, get_redactor
from src.governance.policy import AIPolicy, get_policy
from src.governance.rbac import Principal, anonymous_principal
from src.governance.runtime_flags import get_flags
from src.governance.sensitivity_guard import SensitivityGuard
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import (
    answers_total,
    chunks_retrieved,
    citation_validation,
    query_latency,
    retrieval_empty,
    retrieval_filter_fallback,
    semantic_cache_lookups,
)
from src.monitoring.tracing import get_current_trace_id
from src.retrieval.agents.query_agent import QueryAgent
from src.retrieval.answer.answer_pipeline import AnswerPipeline
from src.retrieval.cache.semantic_cache import SemanticCache
from src.retrieval.context.context_processor import ContextProcessor
from src.retrieval.fusers.fuser import Fuser
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.rerankers.base import Reranker
from src.routing.router import QueryRouter
from src.routing.routes import Route, RouteDecision, RouteSource

logger = get_logger(__name__)

_GENERAL_KNOWLEDGE_PROMPT = """Answer the question below from your own knowledge.

This question was routed away from the document corpus because it does not
require it. Be direct and concise. If you are not confident, say so plainly
rather than guessing — do not invent specifics, and do not cite sources you
were not given.

Question: {query}

Answer:"""


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
    blocked_by_clearance: int = 0


class QueryPipeline:
    """Orchestrates Modules A-G end to end, under governance.

    Two entry points sharing the same A->D retrieval core:
    - inspect(): A -> B -> C -> D only, for /retrieval/inspect.
    - answer(): kill-switch check -> semantic cache -> A -> B -> C -> D ->
      E -> G -> grounding policy check, yielding SSE-ready event dicts, then
      storing the result in cache.

    Governance is applied at three points rather than bolted on at the edge:

      MAP     — the caller's clearance is pushed into the retrieval filters
                before the search runs, and re-checked on the results.
      MEASURE — every stage records to Prometheus, and each answer is
                classified as served / cached / refused / error.
      MANAGE  — the runtime kill switches are read on every request, so an
                operator can stop answering without a redeploy.
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
        sensitivity_guard: SensitivityGuard | None = None,
        policy: AIPolicy | None = None,
        router: QueryRouter | None = None,
        direct_llm: LLMProvider | None = None,
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
        # Defaulted rather than required so existing construction sites and
        # unit tests keep working; production wiring passes both explicitly.
        self._guard = sensitivity_guard or SensitivityGuard()
        self._policy = policy or get_policy()
        # Optional so every existing construction site and unit test keeps
        # working: without a router the pipeline behaves exactly as before,
        # sending every query down the RAG path.
        self._router = router
        self._direct_llm = direct_llm

        from src.config import get_settings

        settings = get_settings()
        self._redact_context_enabled = settings.governance_pii_redact_context
        self._redact_answers = settings.governance_pii_redact_answers
        self._redact_questions = settings.governance_pii_redact_questions

    async def inspect(
        self,
        query: str,
        user_id: uuid.UUID | None = None,
        principal: Principal | None = None,
    ) -> RetrievalInspection:
        # /retrieval/inspect embeds the query exactly as /chat does, so it
        # is the same egress path and needs the same scrub.
        return await self._retrieve(
            self._redact_question(query), user_id, principal or anonymous_principal()
        )

    async def answer(
        self,
        query: str,
        user_id: uuid.UUID | None = None,
        principal: Principal | None = None,
    ) -> AsyncIterator[dict]:
        """Answer a query, guaranteeing every `done` event is fully stamped.

        Scrubbing happens here, before anything touches the query, because the
        question is *also* embedded -- and embedding sends it to the same
        third-party provider. Redacting only at prompt time would close one
        egress path and leave the other open.

        The stamping is done in this wrapper rather than at each `yield`
        because there are five places a `done` event can originate (cache hit,
        kill switch, tool route, refusal, RAG) and two of them previously
        forgot the question -- so a *refused* answer persisted the raw text.
        A single choke point makes that class of omission unrepresentable.

        The redacted value is a local, not instance state: this pipeline is a
        process-wide singleton, so stashing the current question on `self`
        would let concurrent requests overwrite each other's.
        """
        redacted = self._redact_question(query)

        async for event in self._answer_impl(redacted, user_id, principal):
            if event.get("type") == "done":
                event.setdefault("question", redacted)
                event.setdefault("route", Route.RAG.value)
                event.setdefault("route_source", "")
                # A path that never set this did not ground its answer in
                # retrieved documents -- a refusal or an operator stop.
                event.setdefault("grounded", False)
                event.setdefault("context_texts", [])
            yield event

    async def _answer_impl(
        self,
        query: str,
        user_id: uuid.UUID | None = None,
        principal: Principal | None = None,
    ) -> AsyncIterator[dict]:
        principal = principal or anonymous_principal()
        trace_id = get_current_trace_id()
        start = time.perf_counter()

        flags = await get_flags()
        if not flags.answering_enabled:
            # MANAGE: an operator has stopped answering. Return the refusal
            # as a normal `done` event rather than an error so clients render
            # it as a message instead of a failed request.
            answers_total.labels(outcome="disabled").inc()
            logger.warning("answering_disabled_by_kill_switch", trace_id=trace_id)
            yield {
                "type": "done",
                "answer": "Answering is temporarily disabled by an administrator.",
                "citations": [],
                "cached": False,
                "trace_id": trace_id,
                "refused": True,
                "refusal_reason": "kill_switch",
            }
            return

        if flags.semantic_cache_enabled:
            cached = await self._semantic_cache.lookup(query)
            semantic_cache_lookups.labels(result="hit" if cached else "miss").inc()
            if cached is not None:
                answers_total.labels(outcome="cached").inc()
                query_latency.labels(intent="cached", provider="cache").observe(
                    time.perf_counter() - start
                )
                yield {
                    "type": "done",
                    "answer": cached.answer,
                    "citations": cached.citations,
                    "cached": True,
                    "trace_id": trace_id,
                    "route": "cached",
                    "route_source": "cache",
                    # A cached answer is grounded only if it carried citations
                    # when it was stored -- a cached tool answer did not.
                    "grounded": bool(cached.citations),
                    "refused": False,
                    "context_texts": [],
                }
                return
        else:
            semantic_cache_lookups.labels(result="disabled").inc()

        # ROUTE: decide how to answer before paying to answer. Routes that
        # need no documents never touch embedding, vector search, BM25,
        # fusion, reranking or compression.
        decision = (
            await self._router.route(query)
            if self._router is not None
            else RouteDecision(
                route=Route.RAG, source=RouteSource.FORCED, reason="no router configured"
            )
        )

        if decision.route is not Route.RAG:
            async for event in self._answer_off_pipeline(
                query, decision, principal, trace_id, start, flags
            ):
                yield event
            return

        async for event in self._answer_via_rag(query, principal, trace_id, start, flags, user_id):
            yield event

    async def _answer_via_rag(
        self,
        query: str,
        principal: Principal,
        trace_id: str,
        start: float,
        flags,
        user_id: uuid.UUID | None = None,
    ) -> AsyncIterator[dict]:
        """The full retrieval pipeline: Modules A–G under the grounding policy.

        Extracted from `answer()` so the router can reach it as a fallback
        when a tool it selected turns out to be unavailable — the user should
        get an answer, not the router's misjudgement.
        """
        inspection = await self._retrieve(query, user_id, principal)
        intent = inspection.processed_query.intent.type.value
        provider = self._answer_pipeline.model_id

        compressed_chunks, citations = await self._context_processor.process(
            inspection.processed_query.rewritten_query, inspection.reranked_results
        )

        # MAP: scrub personal data from retrieved passages *before* they enter
        # the prompt. Once a passage is in the prompt it has left the
        # deployment, so redacting the answer alone would be too late.
        compressed_chunks = self._redact_context(compressed_chunks)

        # GOVERN (C-GOV-03): nothing retrieved means the model has no grounds
        # to answer from. Checked *before* generation so an ungrounded answer
        # is never produced -- and never billed for -- rather than generated
        # and then suppressed.
        pre_decision = self._policy.check_grounding(
            context_chunks=len(compressed_chunks), valid_citations=0
        )
        if pre_decision.denied and pre_decision.control_id == "C-GOV-03":
            async for event in self._refuse(pre_decision, trace_id, intent, provider, start):
                yield event
            return

        answer_text = ""
        citation_payload: list[dict] = []
        errored = False

        async for event in self._answer_pipeline.generate(
            query=inspection.processed_query.rewritten_query,
            chunks=compressed_chunks,
            citations=citations,
        ):
            if event["type"] == "error":
                errored = True
                answers_total.labels(outcome="error").inc()
                event["trace_id"] = trace_id
                yield event
                return

            if event["type"] != "done":
                yield event
                continue

            answer_text = event["answer"]
            citation_payload = event["citations"]

            # GOVERN (C-GOV-04): the answer exists but cites nothing that was
            # retrieved. CitationValidator has already discarded fabricated
            # markers, so an empty list here means the model either invented
            # every citation or ignored the context entirely.
            post_decision = self._policy.check_grounding(
                context_chunks=len(compressed_chunks),
                valid_citations=len(citation_payload),
            )
            if post_decision.denied:
                citation_validation.labels(result="uncited_answer").inc()
                if self._policy.enforcing:
                    async for refusal in self._refuse(
                        post_decision, trace_id, intent, provider, start
                    ):
                        yield refusal
                    return
                # MONITOR mode: the violation is counted and audited, but the
                # answer still ships. This is the intended rollout path for a
                # new control -- watch the counter before enforcing.
                await audit_record(
                    action=AuditAction.POLICY_VIOLATION,
                    actor_id=principal.user_id,
                    actor_role=principal.role,
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
            event["answer"] = answer_text = self._redact_answer(answer_text)
            event["trace_id"] = trace_id
            event["refused"] = False
            event["route"] = Route.RAG.value
            event["grounded"] = True
            # The redacted question, so the caller persists what was actually
            # sent rather than the raw text it received.
            event["question"] = query
            # Internal-only key: the exact text the model was grounded on.
            # The online judge needs it to score faithfulness, and the
            # message row needs it for the audit trail. The chat route pops
            # it before serialising, so it never inflates the SSE payload.
            event["context_texts"] = [c.compressed_content for c in compressed_chunks]
            yield event

            answers_total.labels(outcome="served").inc()
            if flags.semantic_cache_enabled:
                await self._semantic_cache.store(
                    query,
                    answer_text,
                    citation_payload,
                    model_used=event.get("model_used", ""),
                )

        if not errored:
            query_latency.labels(intent=intent, provider=provider).observe(
                time.perf_counter() - start
            )

    async def _answer_off_pipeline(
        self,
        query: str,
        decision: RouteDecision,
        principal: Principal,
        trace_id: str,
        start: float,
        flags,
    ) -> AsyncIterator[dict]:
        """Answer a query the router sent somewhere other than RAG.

        Every branch emits the same `done` event shape the RAG path does, so
        the chat route, the UI and the online evaluator need no special
        casing for routed answers.
        """
        route = decision.route

        if route is Route.REFUSE:
            answers_total.labels(outcome="refused").inc()
            yield self._done(
                "I need an actual question to answer.",
                decision,
                trace_id,
                refused=True,
            )
            return

        if route is Route.GREETING:
            # Zero model calls, zero retrieval. The cheapest possible answer
            # to the most common non-question in real traffic.
            answers_total.labels(outcome="served").inc()
            query_latency.labels(intent="greeting", provider="none").observe(
                time.perf_counter() - start
            )
            yield self._done(
                str(decision.args.get("reply") or "Hello."), decision, trace_id
            )
            return

        if route is Route.LLM_KNOWLEDGE:
            async for event in self._answer_from_model(query, decision, trace_id, start):
                yield event
            return

        tool_name = route.tool_name
        if not tool_name:
            logger.error("route_has_no_handler", route=route.value)
            answers_total.labels(outcome="error").inc()
            yield {
                "type": "error",
                "code": "unroutable",
                "message": f"No handler for route {route.value}.",
                "trace_id": trace_id,
            }
            return

        result = await self._run_tool(tool_name, query, decision, principal, trace_id)
        if not result.succeeded:
            # A tool failure falls through to the full pipeline rather than
            # surfacing an error: the router's guess was that a tool could
            # answer this, and being wrong should cost latency, not an answer.
            logger.info("tool_failed_falling_back_to_rag", tool=tool_name, error=result.error)
            async for event in self._answer_via_rag(query, principal, trace_id, start, flags):
                yield event
            return

        answer = self._redact_answer(result.answer)
        answers_total.labels(outcome="served").inc()
        query_latency.labels(intent=route.value, provider=tool_name).observe(
            time.perf_counter() - start
        )

        if result.cacheable and flags.semantic_cache_enabled:
            await self._semantic_cache.store(query, answer, result.citations, model_used=tool_name)

        yield self._done(answer, decision, trace_id, citations=result.citations)

    async def _run_tool(
        self,
        tool_name: str,
        query: str,
        decision: RouteDecision,
        principal: Principal,
        trace_id: str,
    ):
        from src.tools.base import ToolRequest, ToolResult
        from src.tools.registry import get_tool

        try:
            tool = get_tool(tool_name)
        except Exception as exc:
            logger.warning("tool_unavailable", tool=tool_name, error=str(exc))
            return ToolResult(answer="", succeeded=False, error=str(exc), cacheable=False)

        return await tool.run(
            ToolRequest(
                query=query, principal=principal, args=dict(decision.args), trace_id=trace_id
            )
        )

    async def _answer_from_model(
        self, query: str, decision: RouteDecision, trace_id: str, start: float
    ) -> AsyncIterator[dict]:
        """General knowledge: one model call, no retrieval, no citations.

        The answer is explicitly marked as not document-sourced. Presenting
        model knowledge with the same authority as a cited passage is exactly
        the confusion a RAG system exists to prevent, so the distinction is
        carried in the event rather than left to the reader.
        """
        if self._direct_llm is None:
            logger.warning("direct_llm_unavailable_falling_back")
            decision = RouteDecision(
                route=Route.RAG, source=RouteSource.FALLBACK, reason="no direct LLM configured"
            )
            return

        tokens: list[str] = []
        try:
            async for token in self._direct_llm.stream(
                _GENERAL_KNOWLEDGE_PROMPT.format(query=query), max_tokens=800, temperature=0.3
            ):
                tokens.append(token)
                yield {"type": "token", "content": token}
        except Exception as exc:
            answers_total.labels(outcome="error").inc()
            logger.error("direct_llm_failed", error=str(exc))
            yield {
                "type": "error",
                "code": "generation_failed",
                "message": str(exc),
                "trace_id": trace_id,
            }
            return

        answer = self._redact_answer("".join(tokens))
        answers_total.labels(outcome="served").inc()
        query_latency.labels(
            intent="llm_knowledge", provider=self._direct_llm.model_id
        ).observe(time.perf_counter() - start)
        yield self._done(answer, decision, trace_id, model_used=self._direct_llm.model_id)

    def _done(
        self,
        answer: str,
        decision: RouteDecision,
        trace_id: str,
        *,
        citations: list[dict] | None = None,
        refused: bool = False,
        model_used: str = "",
    ) -> dict:
        return {
            "type": "done",
            "answer": answer,
            "citations": citations or [],
            "cached": False,
            "trace_id": trace_id,
            "refused": refused,
            "route": decision.route.value,
            "route_source": decision.source.value,
            # No documents were consulted, so the answer is not grounded in
            # the corpus. Clients should say so rather than implying it was.
            "grounded": False,
            "model_used": model_used,
            "context_texts": [],
        }

    def _redact_question(self, query: str) -> str:
        if not query or not self._policy.pii_redaction_enabled or not self._redact_questions:
            return query
        result = get_question_redactor().redact(query, surface="question")
        if result.redacted:
            logger.info("question_redacted", **result.counts)
        return result.text

    def _redact_answer(self, answer: str) -> str:
        if not answer or not self._policy.pii_redaction_enabled or not self._redact_answers:
            return answer
        return get_redactor().redact(answer, surface="answer").text

    def _redact_context(self, chunks: list[CompressedChunk]) -> list[CompressedChunk]:
        """Mask personal data in retrieved passages before they reach the model.

        Rebuilds each CompressedChunk rather than mutating it: the same
        underlying chunk objects are referenced by the citation map and by
        `/retrieval/inspect`, and mutating in place would silently change
        what those report.
        """
        if not self._policy.pii_redaction_enabled or not self._redact_context_enabled:
            return chunks

        redactor = get_redactor()
        redacted: list[CompressedChunk] = []
        for chunk in chunks:
            result = redactor.redact(chunk.compressed_content, surface="context")
            redacted.append(
                CompressedChunk(
                    reranked=chunk.reranked,
                    compressed_content=result.text,
                    token_count=chunk.token_count,
                )
                if result.redacted
                else chunk
            )
        return redacted

    def describe_config(self) -> dict:
        """The retrieval configuration actually in force.

        Recorded against every evaluation run so a score can be attributed to
        the configuration that produced it. The previous hard-coded
        `{"reranker": "passthrough"}` was wrong for every real run, which
        made cross-run comparison unsound: a change in scores could not be
        told apart from a change in configuration.
        """
        return {
            "vector_top_k": self._vector_top_k,
            "bm25_top_k": self._bm25_top_k,
            "rerank_top_n": self._rerank_top_n,
            "reranker": type(self._reranker).__name__,
            "generator_model": self._answer_pipeline.model_id,
            "policy_version": self._policy.version,
            "enforcement_mode": self._policy.enforcement_mode.value,
        }

    async def invalidate_cached_document(self, document_id: uuid.UUID) -> None:
        """Drop cached answers derived from a document that was deleted or
        reclassified. Exposed here so routes never reach past the pipeline
        into the cache directly."""
        await self._semantic_cache.invalidate_document(document_id)

    async def _refuse(
        self,
        decision,
        trace_id: str,
        intent: str,
        provider: str,
        start: float,
    ) -> AsyncIterator[dict]:
        """Emit a policy refusal as a well-formed `done` event.

        A refusal is a governed outcome, not a failure: it is counted,
        audited against the control that produced it, and returned in the
        same shape as a successful answer so no client needs special
        handling to display it.
        """
        answers_total.labels(outcome="refused").inc()
        query_latency.labels(intent=intent, provider=provider).observe(
            time.perf_counter() - start
        )
        logger.warning(
            "answer_refused",
            control_id=decision.control_id,
            reason=decision.reason,
            trace_id=trace_id,
        )
        await audit_record(
            action=AuditAction.ANSWER_REFUSED,
            resource_type="answer",
            outcome=AuditOutcome.DENIED,
            reason=decision.reason,
            control_id=decision.control_id,
        )
        yield {
            "type": "done",
            "answer": AIPolicy.REFUSAL_MESSAGE,
            "citations": [],
            "cached": False,
            "trace_id": trace_id,
            "refused": True,
            "refusal_reason": decision.control_id,
        }

    async def _retrieve(
        self, query: str, user_id: uuid.UUID | None, principal: Principal
    ) -> RetrievalInspection:
        start = time.perf_counter()
        processed = await self._query_agent.process(query, user_id=user_id)
        query_processing_ms = int((time.perf_counter() - start) * 1000)

        # MAP: overwrite whatever FilterGenerator produced for this field.
        # The clearance allow-list is derived from the authenticated
        # principal, never from the query or the LLM's interpretation of it.
        processed.filters.apply_clearance(
            Sensitivity.values_at_or_below(principal.clearance)
        )

        vector_results, bm25_results, trace = await self._hybrid_retriever.retrieve(
            queries=processed.all_queries,
            vector_top_k=self._vector_top_k,
            bm25_top_k=self._bm25_top_k,
            vector_filter=processed.filters.to_vector_filter(),
            bm25_filter=processed.filters.to_bm25_filter(),
        )
        trace.query_processing_ms = query_processing_ms

        # FilterGenerator infers `domain` and `tags` from the wording of the
        # question, with no knowledge of which values exist in the corpus.
        # Those are applied as hard AND constraints, so one wrong guess
        # ("domain=Sales" for an Operations document) makes the entire corpus
        # invisible and the system answers "no supporting passage was
        # retrieved" while sitting on the exact document asked about.
        #
        # So: if the narrowed search found nothing, retry once with the
        # precision hints dropped. The access controls -- tenant and
        # classification -- are deliberately NOT dropped; `security_only()`
        # exists so this path cannot relax them even by mistake.
        if not vector_results and not bm25_results and processed.filters.has_soft_filters:
            logger.info(
                "retrieval_retry_without_soft_filters",
                domain=processed.filters.domain,
                tags=processed.filters.tags,
                file_type=processed.filters.file_type,
            )
            relaxed = processed.filters.security_only()
            vector_results, bm25_results, retry_trace = await self._hybrid_retriever.retrieve(
                queries=processed.all_queries,
                vector_top_k=self._vector_top_k,
                bm25_top_k=self._bm25_top_k,
                vector_filter=relaxed.to_vector_filter(),
                bm25_filter=relaxed.to_bm25_filter(),
            )
            trace.vector_search_ms += retry_trace.vector_search_ms
            trace.bm25_search_ms += retry_trace.bm25_search_ms
            retrieval_filter_fallback.labels(
                recovered=str(bool(vector_results or bm25_results)).lower()
            ).inc()

        # Defence in depth behind the backend pre-filters -- see
        # SensitivityGuard for why both layers exist.
        vector_results, blocked_vector = self._guard.filter(vector_results, principal.clearance)
        bm25_results, blocked_bm25 = self._guard.filter(bm25_results, principal.clearance)
        trace.vector_count = len(vector_results)
        trace.bm25_count = len(bm25_results)

        chunks_retrieved.labels(stage="vector").observe(len(vector_results))
        chunks_retrieved.labels(stage="bm25").observe(len(bm25_results))

        fusion_start = time.perf_counter()
        fused = await self._fuser.fuse(vector_results, bm25_results)
        trace.fusion_ms = int((time.perf_counter() - fusion_start) * 1000)
        trace.fused_count = len(fused)
        chunks_retrieved.labels(stage="fused").observe(len(fused))

        rerank_start = time.perf_counter()
        reranked = await self._reranker.rerank(
            processed.rewritten_query, fused, top_n=self._rerank_top_n
        )
        trace.reranking_ms = int((time.perf_counter() - rerank_start) * 1000)
        trace.reranked_count = len(reranked)
        trace.total_ms = int((time.perf_counter() - start) * 1000)
        chunks_retrieved.labels(stage="reranked").observe(len(reranked))

        if not reranked:
            # Watched by risk R-T02 (retrieval miss). A rising rate here is
            # the earliest signal that the corpus no longer covers what users
            # are asking -- visible long before answer quality scores move.
            retrieval_empty.inc()

        return RetrievalInspection(
            processed_query=processed,
            vector_results=vector_results,
            bm25_results=bm25_results,
            fused_results=fused,
            reranked_results=reranked,
            trace=trace,
            blocked_by_clearance=blocked_vector + blocked_bm25,
        )
