from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from src.domain.repositories.project_repository import ProjectRepository
from src.domain.repositories.search_repository import BM25ScoredChunk
from src.domain.repositories.vector_repository import ScoredChunk
from src.domain.value_objects.context_bundle import CompressedChunk
from src.domain.value_objects.processed_query import ProcessedQuery
from src.domain.value_objects.retrieval_candidate import FusedChunk, RerankedChunk
from src.domain.value_objects.retrieval_trace import RetrievalTrace
from src.domain.value_objects.sensitivity import Sensitivity
from src.governance.audit import AuditAction, AuditOutcome
from src.governance.audit import record as audit_record
from src.governance.pii import get_question_redactor, get_redactor
from src.governance.policy import AIPolicy, get_policy
from src.governance.rbac import Principal, anonymous_principal
from src.governance.sensitivity_guard import SensitivityGuard
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.prometheus_metrics import (
    answers_total,
    chunks_retrieved,
    query_latency,
    retrieval_empty,
    retrieval_filter_fallback,
)
from src.monitoring.tracing import get_current_trace_id
from src.retrieval.agents.query_agent import QueryAgent
from src.retrieval.answer.answer_pipeline import AnswerPipeline
from src.retrieval.cache.semantic_cache import SemanticCache
from src.retrieval.context.context_processor import ContextProcessor
from src.retrieval.fusers.fuser import Fuser
from src.retrieval.graph.state import QueryState
from src.retrieval.hybrid_retriever import HybridRetriever
from src.retrieval.rerankers.base import Reranker
from src.routing.router import QueryRouter
from src.routing.routes import Route, RouteDecision

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
        project_repo: ProjectRepository | None = None,
        latest_only: bool = True,
        vision_fallback: Any = None,
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
        # Optional for the same reason as the router: without it the graph's
        # vision node is a no-op and every answer is deterministic, which is
        # exactly the behaviour a deployment without a vision-capable
        # provider should get.
        self._vision_fallback = vision_fallback
        # Optional so every existing construction site and unit test keeps
        # working: with no project repository the access scope is owner-only,
        # exactly as it was before projects existed.
        self._project_repo = project_repo
        self._latest_only = latest_only

        from src.config import get_settings

        settings = get_settings()
        self._redact_context_enabled = settings.governance_pii_redact_context
        self._redact_answers = settings.governance_pii_redact_answers
        self._redact_questions = settings.governance_pii_redact_questions

        # Built last: the graph closes over this instance, so every component
        # above must already be assigned.
        from src.retrieval.graph import build_query_graph

        self._graph = build_query_graph(self)

    async def _project_ids_for(self, principal: Principal) -> list[uuid.UUID] | None:
        """The caller's project memberships, or None when projects are unused.

        Failure here is deliberately *closed*: if membership cannot be read,
        the caller falls back to owner-only reach rather than to unfiltered
        access. A degraded lookup must narrow what is visible, never widen it.
        """
        if self._project_repo is None or principal.user_id is None:
            return None
        try:
            return await self._project_repo.member_project_ids(principal.user_id)
        except Exception as exc:
            logger.warning(
                "project_scope_lookup_failed",
                user_id=str(principal.user_id),
                error=str(exc),
                effect="falling back to owner-only reach",
            )
            return None

    def _general_knowledge_prompt(self, query: str) -> str:
        return _GENERAL_KNOWLEDGE_PROMPT.format(query=query)

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
        history: Sequence[tuple[str, str]] | None = None,
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

        async for event in self._answer_impl(redacted, user_id, principal, history):
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
        history: Sequence[tuple[str, str]] | None = None,
    ) -> AsyncIterator[dict]:
        """Run the query graph, forwarding its custom stream as SSE events.

        The control flow -- kill switch, cache, routing, retrieval, context,
        grounding checks, generation, cache store, and the fallback from a
        failed tool back into full retrieval -- lives in
        `src/retrieval/graph/`. It used to be five async generators
        delegating to one another with early returns at eight points; the
        behaviour is unchanged, but the branching is now edges you can read
        in one place.

        `stream_mode="custom"` carries exactly the dicts the nodes write, so
        the event contract the chat route and the UI consume is untouched.
        """
        state: QueryState = {
            "query": query,
            "user_id": user_id,
            "principal": principal or anonymous_principal(),
            "trace_id": get_current_trace_id(),
            "started_at": time.perf_counter(),
            "history": list(history or ()),
        }

        async for event in self._graph.astream(state, stream_mode="custom"):
            yield event

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
        query_latency.labels(intent=intent, provider=provider).observe(time.perf_counter() - start)
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
        self,
        query: str,
        user_id: uuid.UUID | None,
        principal: Principal,
        history: Sequence[tuple[str, str]] | None = None,
    ) -> RetrievalInspection:
        start = time.perf_counter()
        processed = await self._query_agent.process(query, user_id=user_id, history=history)
        query_processing_ms = int((time.perf_counter() - start) * 1000)

        # MAP: overwrite whatever FilterGenerator produced for these fields.
        # Both are derived from the authenticated principal, never from the
        # query or the LLM's interpretation of it.
        #
        # Clearance decides depth (which classifications may be read);
        # access scope decides reach (whose documents are visible at all).
        # They are independent, and both must pass -- a project member still
        # cannot read a `restricted` document above their clearance.
        processed.filters.apply_clearance(Sensitivity.values_at_or_below(principal.clearance))
        processed.filters.apply_access_scope(
            user_id=user_id, project_ids=await self._project_ids_for(principal)
        )

        # Answering from a superseded drawing is worse than not answering, so
        # current revisions are the default. Documents that are not revisions
        # of anything are current by definition and are unaffected.
        processed.filters.latest_only = self._latest_only

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
