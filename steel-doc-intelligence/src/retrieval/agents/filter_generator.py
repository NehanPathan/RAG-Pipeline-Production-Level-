from __future__ import annotations

import time
import uuid
from datetime import date, timedelta
from typing import Protocol

from src.domain.value_objects.metadata_filter import MetadataFilterSpec
from src.domain.value_objects.query_intent import QueryIntent
from src.llm.json_parsing import parse_json_response
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.monitoring.stage_tracer import traced_stage

logger = get_logger(__name__)


class VocabularyProvider(Protocol):
    """The corpus's own keyword vocabulary. Satisfied by SearchRepository."""

    async def aggregate_facets(
        self, fields: list[str], filters: object = None, max_values: int = 50
    ) -> dict[str, list[tuple[str, int]]]: ...


FILTER_PROMPT = """\
Extract structured search constraints implied by the following query.

Return ONLY valid JSON with these fields:
- tags: list of relevant keyword tags implied by the query (lowercase, empty list if none)
- file_type: one of [pdf, docx, txt, md, html] if the query implies a specific document format, else null
- recency_days: integer number of days if the query implies "recent"/"latest"/"this year" etc, else null

Query: {query}

JSON response:"""


class FilterGenerator:
    """Turns a query into metadata filters, using only tags that exist.

    A generated tag is a *guess about wording*, and the model has no idea
    what the corpus is actually tagged with. Applied as a hard filter, a
    guess that matches nothing excludes everything: asking "what
    bolt-related information is present?" produced `tags=[bolts, drawing]`,
    neither of which any chunk carries, so BM25 returned zero rows from an
    index holding fourteen matches for the word. Retrieval then had nothing
    to ground on and the answer was a refusal -- about a drawing that says
    `ALL BOLTS 3/4" DIA. A325` in plain text.

    So generated tags are validated against the corpus vocabulary before
    they become filters. A tag nobody has used is dropped rather than
    enforced: a precision hint that cannot match is not a hint, it is a
    silent no-op that looks like an empty corpus.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        vocabulary: VocabularyProvider | None = None,
        vocabulary_ttl_seconds: float = 300.0,
    ) -> None:
        self._llm = llm_provider
        self._vocabulary = vocabulary
        self._vocabulary_ttl = vocabulary_ttl_seconds
        #: lowercase tag -> the corpus's own spelling of it.
        self._known_tags: dict[str, str] | None = None
        self._known_tags_at = 0.0

    async def _valid_tags(self, tags: list[str] | None) -> list[str] | None:
        """Keep only tags the corpus actually uses.

        Without a vocabulary provider nothing is validated and the previous
        behaviour stands -- the check is an improvement where it can be made,
        not a new hard dependency for every caller.
        """
        if not tags or self._vocabulary is None:
            return tags

        now = time.monotonic()
        if self._known_tags is None or now - self._known_tags_at > self._vocabulary_ttl:
            try:
                facets = await self._vocabulary.aggregate_facets(["tags"])
                # Keyed by lowercase for matching, valued with the corpus's
                # own spelling for filtering. Both halves matter: the model
                # emits lowercase, the index stores `S-207`, and a keyword
                # filter is case-sensitive -- so validating case-insensitively
                # and then applying the lowercased form matches nothing, which
                # is precisely the silent empty-result failure this guard
                # exists to prevent.
                self._known_tags = {value.lower(): value for value, _ in facets.get("tags", [])}
                self._known_tags_at = now
            except Exception as exc:
                # Unknown vocabulary means unvalidated tags. Applying them
                # anyway risks the empty-result failure this exists to
                # prevent, so they are dropped: a broader search beats a
                # silently empty one.
                logger.warning("tag_vocabulary_unavailable", error=str(exc))
                return None

        # Substituted, not merely filtered: the value applied has to be the
        # one the index actually holds.
        kept = [self._known_tags[t] for t in tags if t in self._known_tags]
        if len(kept) != len(tags):
            logger.info(
                "filter_tags_dropped",
                dropped=[t for t in tags if t not in self._known_tags],
                kept=kept,
            )
        return kept or None

    async def generate(
        self,
        query: str,
        intent: QueryIntent,
        selected_sources: list[str],
        user_id: uuid.UUID | None = None,
    ) -> MetadataFilterSpec:
        async with traced_stage("filter_generation", query=query) as stage:
            spec = await self._generate(query, selected_sources, user_id)
            stage.set_result(domain=spec.domain, tags=spec.tags, file_type=spec.file_type)
            return spec

    async def _generate(
        self,
        query: str,
        selected_sources: list[str],
        user_id: uuid.UUID | None,
    ) -> MetadataFilterSpec:
        domain = selected_sources[0] if selected_sources else None
        fallback = MetadataFilterSpec(user_id=user_id, domain=domain)

        try:
            response = await self._llm.complete(
                prompt=FILTER_PROMPT.format(query=query), max_tokens=200, temperature=0.0
            )
            data = parse_json_response(response)
            if not isinstance(data, dict):
                return fallback

            tags = [
                str(t).strip().lower() for t in data.get("tags") or [] if str(t).strip()
            ] or None
            file_type = data.get("file_type") or None
            recency_days = data.get("recency_days")
            date_from = date.today() - timedelta(days=int(recency_days)) if recency_days else None

            return MetadataFilterSpec(
                user_id=user_id,
                domain=domain,
                tags=await self._valid_tags(tags),
                file_type=file_type,
                date_from=date_from,
            )
        except Exception as e:
            logger.warning("filter_generation_failed", query=query[:100], error=str(e))
            return fallback
