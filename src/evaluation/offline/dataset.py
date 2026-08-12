from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class EvalSample:
    question: str
    ground_truth: str | None = None
    notes: str | None = None


@dataclass
class EvalDataset:
    name: str
    samples: list[EvalSample] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.samples)


_DATASET_DIR = Path(__file__).parents[3] / "data" / "eval_datasets"


def load_dataset(name: str) -> EvalDataset:
    """Load a golden dataset JSON file from data/eval_datasets/<name>.json.

    Each file is a JSON array of objects with keys:
      - question  (required)
      - ground_truth  (optional)
      - notes  (optional)
    """
    path = _DATASET_DIR / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(
            f"Eval dataset not found: {path}. "
            "Run `make seed` or `python scripts/seed_eval_dataset.py` to create it."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    samples = [
        EvalSample(
            question=item["question"],
            ground_truth=item.get("ground_truth"),
            notes=item.get("notes"),
        )
        for item in raw
    ]
    return EvalDataset(name=name, samples=samples)


def list_datasets() -> list[str]:
    """Return the names (without .json extension) of all available datasets."""
    if not _DATASET_DIR.exists():
        return []
    return [p.stem for p in sorted(_DATASET_DIR.glob("*.json"))]
