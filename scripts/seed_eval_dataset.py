"""Seed evaluation datasets.

Usage:
    uv run python scripts/seed_eval_dataset.py

Writes data/eval_datasets/golden_set_v1.json (does not overwrite if it exists).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
DATASET_DIR = ROOT / "data" / "eval_datasets"
DATASET_DIR.mkdir(parents=True, exist_ok=True)

GOLDEN_V1 = [
    {
        "question": "What are the main causes of machine learning failures in production?",
        "ground_truth": (
            "Machine learning systems commonly fail in production due to data drift, "
            "training-serving skew, poor monitoring, lack of feedback loops, "
            "underestimation of infrastructure requirements, and inadequate testing "
            "on real-world edge cases."
        ),
        "notes": "Tests faithfulness and retrieval quality on technical ML content",
    },
    {
        "question": "How can you detect and handle data drift in a deployed ML model?",
        "ground_truth": (
            "Data drift can be detected by monitoring the statistical distribution of "
            "incoming features against the training distribution using metrics like PSI, "
            "KS-test, or KL divergence. Handling strategies include automatic retraining "
            "pipelines, shadow deployments, and alerting systems."
        ),
        "notes": "Tests context recall on monitoring and MLOps topics",
    },
    {
        "question": "What is the difference between online and offline evaluation for RAG systems?",
        "ground_truth": (
            "Offline evaluation uses pre-built golden datasets with known answers to measure "
            "faithfulness, relevancy, and correctness. Online evaluation measures real-user "
            "metrics like feedback ratings and latency in production."
        ),
        "notes": "General RAG knowledge",
    },
    {
        "question": "What strategies improve retrieval quality in a hybrid search system?",
        "ground_truth": (
            "Improvements include query rewriting, query expansion, fine-tuned embeddings, "
            "reciprocal rank fusion, cross-encoder reranking, and parent-child chunking."
        ),
        "notes": "Retrieval pipeline knowledge",
    },
    {
        "question": "Describe best practices for chunking documents for RAG applications.",
        "ground_truth": (
            "Best practices: semantic boundaries, parent-child chunks, appropriate overlap, "
            "and metadata enrichment to support filtered search."
        ),
        "notes": "Chunking strategy",
    },
]

target = DATASET_DIR / "golden_set_v1.json"
if target.exists():
    print(f"[skip] {target} already exists — remove it first to regenerate.")
    sys.exit(0)

target.write_text(json.dumps(GOLDEN_V1, indent=2, ensure_ascii=False), encoding="utf-8")
print(f"[ok] Wrote {len(GOLDEN_V1)} samples to {target}")
