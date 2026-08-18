from __future__ import annotations

from collections.abc import Sequence

from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.stage_tracer import traced_stage

logger = get_logger(__name__)

REWRITE_PROMPT = """\
Rewrite the following user query to be clear, grammatically correct, and \
fully self-contained, without changing its meaning or adding information \
that isn't implied by the original. Return ONLY the rewritten query text, \
with no extra commentary or quotes.

Query: {query}

Rewritten query:"""

FOLLOW_UP_PROMPT = """\
You are resolving a follow-up question in an ongoing conversation about \
engineering drawings.

Rewrite the LATEST question so it stands on its own, replacing pronouns and \
elliptical references ("it", "its", "that one", "what about the bolts") with \
the drawing, revision or subject they refer to earlier in the conversation.

Rules:
- Change nothing but the references. Do not answer the question, add detail, \
or narrow it.
- If the latest question is already self-contained, return it unchanged.
- If the conversation does not make the reference clear, return it unchanged \
rather than guessing.
- Return ONLY the rewritten question.

Conversation so far:
{history}

Latest question: {query}

Rewritten question:"""


class QueryRewriter:
    def __init__(self, llm_provider: LLMProvider, max_history_turns: int = 6) -> None:
        self._llm = llm_provider
        # Bounded on purpose. Sending the whole conversation grows every
        # request without bound, costs more on each turn than the last, and
        # eventually pushes the retrieved passages out of the context window
        # -- the answer degrading as the conversation gets *more* informative.
        # A reference like "the bolts" points at something recent.
        self._max_history_turns = max_history_turns

    async def rewrite(self, query: str, history: Sequence[tuple[str, str]] | None = None) -> str:
        """Make the query self-contained, resolving it against recent turns.

        `history` is (role, text) oldest-first. Without it, "What about the
        bolts?" is retrieved literally: it names no drawing, matches nothing
        in particular, and the honest answer is a refusal. Resolving it to
        "What bolt information is in SSD09.0-02?" is what makes a
        conversation possible at all.

        Resolution happens here rather than in a new component because this
        is already the stage that makes a query self-contained, and routing
        downstream should see one query whatever produced it.
        """
        async with traced_stage("query_rewrite", query=query) as stage:
            turns = list(history or ())[-self._max_history_turns :]
            rewritten = (
                await self._resolve_follow_up(query, turns) if turns else await self._rewrite(query)
            )
            stage.set_result(rewritten_query=rewritten, history_turns=len(turns))
            return rewritten

    async def _resolve_follow_up(self, query: str, turns: list[tuple[str, str]]) -> str:
        rendered = "\n".join(f"{role}: {text}" for role, text in turns)
        try:
            response = await self._llm.complete(
                prompt=FOLLOW_UP_PROMPT.format(history=rendered, query=query),
                max_tokens=200,
                temperature=0.0,
            )
            resolved = response.strip().strip('"')
            if resolved and resolved.lower() != query.strip().lower():
                logger.info("follow_up_resolved", original=query, resolved=resolved)
            return resolved or query
        except Exception as exc:
            # Falling back to the raw query keeps the turn answerable: a
            # degraded rewrite is a worse query, not a failed request.
            logger.warning("follow_up_resolution_failed", error=str(exc))
            return query

    async def _rewrite(self, query: str) -> str:
        try:
            response = await self._llm.complete(
                prompt=REWRITE_PROMPT.format(query=query), max_tokens=200, temperature=0.0
            )
            rewritten = response.strip().strip('"')
            return rewritten or query
        except Exception as e:
            logger.warning("query_rewrite_failed", query=query[:100], error=str(e))
            return query
