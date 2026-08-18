from __future__ import annotations

import re

from src.config import get_settings
from src.llm.registry import get_llm_provider
from src.tools.base import Tool, ToolError, ToolRequest, ToolResult
from src.tools.registry import tools

_TRANSLATE_RE = re.compile(
    r"^\s*(?:translate|say|how\s+do\s+you\s+say)\s+"
    r"(?P<text>.+?)"
    r"\s+(?:in|to|into)\s+(?P<language>[A-Za-z\s]+?)\s*[?.]?\s*$",
    re.IGNORECASE | re.DOTALL,
)

PROMPT = """Translate the text below into {language}.

Return ONLY the translation. No explanation, no transliteration, no quotes.

Text:
{text}"""


class TranslationTool(Tool):
    """Translation via the small model, with no retrieval.

    A translation request needs the language model's competence but none of
    the corpus, so routing it here skips embedding, vector search, BM25,
    fusion, reranking and compression — the majority of a query's cost and
    latency — while producing a better answer than a RAG prompt would, since
    no retrieved passage is competing for the model's attention.
    """

    name = "translation"
    description = (
        "Translate text between languages. Use when the user asks for a "
        "translation and no document lookup is needed."
    )

    async def execute(self, request: ToolRequest) -> ToolResult:
        text = str(request.args.get("text") or "")
        language = str(request.args.get("language") or "")

        if not text or not language:
            parsed = parse(request.query)
            if parsed is None:
                raise ToolError(
                    "Could not work out what to translate, or into which language."
                )
            text, language = parsed

        llm = get_llm_provider(get_settings(), role="small")
        translated = await llm.complete(
            PROMPT.format(language=language, text=text), max_tokens=800, temperature=0.1
        )
        return ToolResult(
            answer=translated.strip(),
            data={"source_text": text, "target_language": language},
            cacheable=True,
        )


def parse(query: str) -> tuple[str, str] | None:
    """Pull (text, language) out of a natural-language translation request.

    Returns None rather than guessing when the shape does not match — a
    wrong guess here produces a confidently mistranslated answer, which is
    worse than falling through to the normal pipeline.
    """
    match = _TRANSLATE_RE.match(query.strip())
    if not match:
        return None
    text = match.group("text").strip().strip("\"'")
    language = match.group("language").strip()
    if not text or not language:
        return None
    return text, language


def looks_like_translation(query: str) -> bool:
    return parse(query) is not None


@tools.register(
    "translation",
    description=TranslationTool.description,
    tags=("language", "llm"),
)
def _build_translation() -> Tool:
    return TranslationTool()
