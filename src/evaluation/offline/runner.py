from __future__ import annotations

import uuid
from collections import defaultdict

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from src.evaluation.offline.dataset import EvalDataset
from src.evaluation.offline.metrics import (
    score_answer_correctness,
    score_answer_relevancy,
    score_context_relevancy,
    score_faithfulness,
)
from src.evaluation.offline.repository import (
    create_run,
    finish_run,
    save_metrics,
    update_run_progress,
)
from src.llm.providers.base import LLMProvider
from src.monitoring.logger import get_logger
from src.retrieval.pipeline import QueryPipeline

logger = get_logger(__name__)


async def run_evaluation(
    *,
    run_id: uuid.UUID,
    run_name: str,
    dataset: EvalDataset,
    pipeline: QueryPipeline,
    scorer_llm: LLMProvider,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Background task: runs every sample through the RAG pipeline, scores
    each one with the LLM-based metrics, then writes aggregated results to DB.
    """
    async with session_factory() as session:
        await create_run(
            session,
            run_id=run_id,
            name=run_name,
            dataset_name=dataset.name,
            model_config={"scorer": scorer_llm.model_id},
            retrieval_config={"reranker": "passthrough"},
            total_items=len(dataset),
        )

    scores: dict[str, list[float]] = defaultdict(list)

    for i, sample in enumerate(dataset.samples):
        logger.info("eval_sample_start", run_id=str(run_id), sample=i + 1, total=len(dataset))
        try:
            # Run retrieval to get contexts
            inspection = await pipeline.inspect(sample.question)
            contexts = [r.chunk.content for r in inspection.reranked_results[:5]]

            # Run full pipeline to get the generated answer
            answer = ""
            async for event in pipeline.answer(sample.question):
                if event["type"] == "done":
                    answer = event["answer"]
                    break
                elif event["type"] == "error":
                    answer = ""
                    break

            if not answer:
                logger.warning("eval_sample_no_answer", sample=i + 1)
                continue

            # Score
            faith = await score_faithfulness(scorer_llm, answer, contexts)
            relevancy = await score_answer_relevancy(scorer_llm, sample.question, answer)
            ctx_rel = await score_context_relevancy(scorer_llm, sample.question, contexts)

            scores["faithfulness"].append(faith)
            scores["answer_relevancy"].append(relevancy)
            scores["context_relevancy"].append(ctx_rel)

            if sample.ground_truth:
                correctness = await score_answer_correctness(
                    scorer_llm, sample.question, answer, sample.ground_truth
                )
                scores["answer_correctness"].append(correctness)

            logger.info(
                "eval_sample_done",
                sample=i + 1,
                faithfulness=f"{faith:.2f}",
                answer_relevancy=f"{relevancy:.2f}",
                context_relevancy=f"{ctx_rel:.2f}",
            )

        except Exception as exc:
            logger.error("eval_sample_failed", sample=i + 1, error=str(exc))

        async with session_factory() as session:
            await update_run_progress(session, run_id, completed=i + 1)

    if not scores:
        async with session_factory() as session:
            await finish_run(session, run_id, error="No samples scored successfully.")
        return

    aggregated = {k: sum(v) / len(v) for k, v in scores.items()}
    sample_size = max(len(v) for v in scores.values())

    async with session_factory() as session:
        await save_metrics(session, run_id, aggregated, sample_size)
        await finish_run(session, run_id)

    logger.info("eval_run_complete", run_id=str(run_id), metrics=aggregated)
