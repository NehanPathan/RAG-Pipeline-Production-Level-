"""LangSmith datasets and evaluators over the existing judge functions.

LangSmith is used here for what it is genuinely better at than a hand-rolled
harness -- versioned datasets, run comparison over time, and a UI for
inspecting individual failures -- while the scoring itself stays in
`metrics.py`.

Keeping the judges is deliberate. They already encode a decision most
libraries get wrong: an unparseable judge response returns `None` and is
excluded from aggregation, rather than being scored 0.5 and silently made
indistinguishable from a genuinely mediocre answer. Swapping them for
library evaluators would trade that away for no gain.

Nothing here runs unless a LangSmith API key is configured. Evaluation is
opt-in; the local harness in `runner.py` remains the default path and has no
dependency on this module.
"""

from __future__ import annotations

from typing import Any

from src.config import Settings
from src.evaluation.offline.dataset import EvalDataset
from src.monitoring.logger import get_logger

logger = get_logger(__name__)


class LangSmithUnavailableError(RuntimeError):
    """Raised when a LangSmith operation is attempted with no API key."""


def _client(settings: Settings) -> Any:
    if not settings.langsmith_api_key:
        raise LangSmithUnavailableError(
            "LANGSMITH_API_KEY is not set. Evaluation datasets and runs are "
            "opt-in; the local harness in src/evaluation/offline/runner.py "
            "needs no LangSmith account."
        )
    from langsmith import Client

    return Client(api_key=settings.langsmith_api_key, api_url=settings.langsmith_endpoint)


def sync_dataset(settings: Settings, dataset: EvalDataset, name: str | None = None) -> str:
    """Push a golden dataset to LangSmith, creating it if absent.

    Idempotent by example input: re-running adds only questions that are not
    already present, so a dataset can be grown over time (for instance from
    thumbs-down feedback promotions) without duplicating what is there.
    """
    client = _client(settings)
    dataset_name = name or dataset.name

    existing = list(client.list_datasets(dataset_name=dataset_name))
    if existing:
        ls_dataset = existing[0]
    else:
        ls_dataset = client.create_dataset(
            dataset_name=dataset_name,
            description="Golden questions for the steel document corpus.",
        )
        logger.info("langsmith_dataset_created", dataset=dataset_name)

    known = {
        (example.inputs or {}).get("question")
        for example in client.list_examples(dataset_id=ls_dataset.id)
    }

    added = 0
    for sample in dataset.samples:
        if sample.question in known:
            continue
        client.create_example(
            dataset_id=ls_dataset.id,
            inputs={"question": sample.question},
            outputs={"ground_truth": sample.ground_truth},
        )
        added += 1

    logger.info(
        "langsmith_dataset_synced", dataset=dataset_name, added=added, total=len(dataset.samples)
    )
    return str(ls_dataset.id)
