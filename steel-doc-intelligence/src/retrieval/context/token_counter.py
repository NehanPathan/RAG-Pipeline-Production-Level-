from __future__ import annotations

import tiktoken


class TokenCounter:
    """Wraps tiktoken with a safe fallback encoding for unrecognized model
    names — tiktoken's model registry lags behind newer model releases, and
    a missing model name shouldn't break token budgeting.
    """

    _DEFAULT_ENCODING = "cl100k_base"

    def __init__(self, model: str) -> None:
        try:
            self._encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            self._encoding = tiktoken.get_encoding(self._DEFAULT_ENCODING)

    def count(self, text: str) -> int:
        return len(self._encoding.encode(text))
