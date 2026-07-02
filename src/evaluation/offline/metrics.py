from __future__ import annotations

import re

from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger

logger = get_logger(__name__)

_SCORE_RE = re.compile(r"\b([0-9](?:\.[0-9]+)?|10(?:\.0+)?)\b")


def _parse_score(text: str) -> float:
    """Extract the first 0-10 number from an LLM response and normalise to 0-1."""
    m = _SCORE_RE.search(text.strip())
    if not m:
        return 0.5
    raw = float(m.group(1))
    return min(max(raw / 10.0, 0.0), 1.0)


async def score_faithfulness(
    llm: LLMProvider, answer: str, contexts: list[str]
) -> float:
    """0-1: answer is only grounded in the retrieved contexts (no hallucination)."""
    context_block = "\n---\n".join(contexts[:8])  # cap to avoid huge prompts
    prompt = f"""You are an impartial RAG quality evaluator.

RETRIEVED CONTEXTS:
{context_block}

GENERATED ANSWER:
{answer}

Task: Score how faithfully the answer uses ONLY information present in the retrieved contexts.
- 10 = every claim in the answer is directly supported by the contexts
- 5  = some claims are supported, others are not
- 0  = the answer introduces facts not present in the contexts at all

Respond with a single number from 0 to 10."""
    try:
        resp = await llm.complete(prompt, max_tokens=10, temperature=0.0)
        return _parse_score(resp)
    except Exception as exc:
        logger.warning("faithfulness_score_failed", error=str(exc))
        return 0.0


async def score_answer_relevancy(
    llm: LLMProvider, question: str, answer: str
) -> float:
    """0-1: answer directly addresses the question."""
    prompt = f"""You are an impartial RAG quality evaluator.

QUESTION:
{question}

GENERATED ANSWER:
{answer}

Task: Score how relevant and on-topic the answer is to the question.
- 10 = the answer directly and completely addresses the question
- 5  = the answer is partially relevant or only answers part of the question
- 0  = the answer is off-topic or does not address the question at all

Respond with a single number from 0 to 10."""
    try:
        resp = await llm.complete(prompt, max_tokens=10, temperature=0.0)
        return _parse_score(resp)
    except Exception as exc:
        logger.warning("answer_relevancy_score_failed", error=str(exc))
        return 0.0


async def score_answer_correctness(
    llm: LLMProvider, question: str, answer: str, ground_truth: str
) -> float:
    """0-1: answer matches the ground-truth reference (requires ground_truth)."""
    prompt = f"""You are an impartial RAG quality evaluator.

QUESTION:
{question}

REFERENCE ANSWER (ground truth):
{ground_truth}

GENERATED ANSWER:
{answer}

Task: Score how correct and complete the generated answer is compared to the reference answer.
- 10 = the generated answer contains all key facts from the reference and no factual errors
- 5  = the generated answer has some correct facts but misses important ones or has minor errors
- 0  = the generated answer is factually wrong or completely misses the point

Respond with a single number from 0 to 10."""
    try:
        resp = await llm.complete(prompt, max_tokens=10, temperature=0.0)
        return _parse_score(resp)
    except Exception as exc:
        logger.warning("answer_correctness_score_failed", error=str(exc))
        return 0.0


async def score_context_relevancy(
    llm: LLMProvider, question: str, contexts: list[str]
) -> float:
    """0-1: retrieved contexts are relevant to the question."""
    context_block = "\n---\n".join(contexts[:6])
    prompt = f"""You are an impartial RAG quality evaluator.

QUESTION:
{question}

RETRIEVED CONTEXTS:
{context_block}

Task: Score how relevant the retrieved contexts are to the question.
- 10 = all contexts are directly relevant and useful for answering the question
- 5  = some contexts are relevant, others are noise
- 0  = none of the contexts are relevant to the question

Respond with a single number from 0 to 10."""
    try:
        resp = await llm.complete(prompt, max_tokens=10, temperature=0.0)
        return _parse_score(resp)
    except Exception as exc:
        logger.warning("context_relevancy_score_failed", error=str(exc))
        return 0.0
