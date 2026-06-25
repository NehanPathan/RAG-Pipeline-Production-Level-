from __future__ import annotations

from src.domain.value_objects.context_bundle import CompressedChunk
from src.retrieval.context.token_counter import TokenCounter


class TokenBudgetManager:
    """Greedily selects compressed chunks, in existing rank order, until the
    token budget would be exceeded. Doesn't re-rank — chunks arrive already
    ordered most-relevant-first, so stopping at the first chunk that would
    overflow the budget (rather than skipping ahead to a smaller one that
    fits) preserves that ordering and avoids surprising "out of order"
    context.
    """

    def __init__(self, token_counter: TokenCounter, max_tokens: int) -> None:
        self._token_counter = token_counter
        self._max_tokens = max_tokens

    def select(self, chunks: list[CompressedChunk]) -> list[CompressedChunk]:
        selected: list[CompressedChunk] = []
        used_tokens = 0

        for chunk in chunks:
            token_count = self._token_counter.count(chunk.compressed_content)
            if used_tokens + token_count > self._max_tokens:
                break
            chunk.token_count = token_count
            selected.append(chunk)
            used_tokens += token_count

        return selected
