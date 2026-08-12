from __future__ import annotations

from src.domain.value_objects.context_bundle import AssembledContext

ANSWER_SYSTEM_PROMPT = """\
You are a helpful assistant answering questions using ONLY the provided context.
Cite sources inline using the bracketed numbers from the context, e.g. [1], [2].
If the context does not contain enough information to answer, say so explicitly --
do not use outside knowledge or make anything up."""


class PromptBuilder:
    """Combines the system instruction, assembled context, and user query
    into the single prompt string LLMProvider.complete()/stream() expect.
    """

    def build(self, query: str, context: AssembledContext) -> str:
        return (
            f"{ANSWER_SYSTEM_PROMPT}\n\n"
            f"Context:\n{context.formatted_text}\n\n"
            f"Question: {query}\n\n"
            f"Answer:"
        )
