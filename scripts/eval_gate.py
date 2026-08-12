#!/usr/bin/env python
"""Evaluation quality gate — the control that makes quality a merge blocker.

Reads the metrics from an evaluation run and compares them against the floors
in AIPolicy. Exits non-zero when any gated metric is below its floor.

The single most important property here is that the floors come from
`src/governance/policy.py`, the same object the runtime alert and the
`/evaluation/gate` endpoint read. A CI gate with its own copy of the
thresholds drifts from the running system, and then the build passes while
production is failing -- which is worse than having no gate, because it
manufactures confidence.

Two input modes:

    --from-api http://localhost:8000    read the latest completed run over HTTP
    --scores faithfulness=0.81,...      score a set produced elsewhere

The API mode is for a pipeline that runs evaluation against a deployed
environment; the explicit mode is for running the gate on results computed in
the same job. Neither mode invents scores -- if there is no completed run, the
gate fails loudly rather than passing by default.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.governance.policy import get_policy  # noqa: E402


def parse_scores(raw: str) -> dict[str, float]:
    scores: dict[str, float] = {}
    for pair in raw.split(","):
        if not pair.strip():
            continue
        name, _, value = pair.partition("=")
        if not name.strip() or not value.strip():
            raise SystemExit(f"Malformed --scores entry: '{pair}' (expected name=value)")
        scores[name.strip()] = float(value)
    return scores


def fetch_scores(base_url: str, dataset: str, timeout: int) -> dict[str, float]:
    url = f"{base_url.rstrip('/')}/api/v1/evaluation/gate?dataset_name={dataset}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Could not read evaluation results from {url}: {exc}")

    if payload.get("status") == "no_completed_run":
        raise SystemExit(
            f"No completed evaluation run for dataset '{dataset}'. "
            "The gate fails rather than passing on absent evidence."
        )
    return {m["metric"]: m["value"] for m in payload.get("metrics", [])}


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail the build when quality is below policy.")
    parser.add_argument("--from-api", dest="api", help="Base URL of a running API")
    parser.add_argument("--scores", help="Explicit scores, e.g. faithfulness=0.81,answer_relevancy=0.77")
    parser.add_argument("--dataset", default="golden_set_v1")
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument(
        "--warn-only",
        action="store_true",
        help="Report breaches but exit 0 -- for introducing the gate on a codebase "
        "that does not pass it yet. Remove once it is green.",
    )
    args = parser.parse_args()

    if not args.api and not args.scores:
        parser.error("Provide --from-api or --scores.")

    scores = parse_scores(args.scores) if args.scores else fetch_scores(
        args.api, args.dataset, args.timeout
    )
    policy = get_policy()

    print("=" * 68)
    print(f"Evaluation gate — policy v{policy.version}, dataset '{args.dataset}'")
    print("=" * 68)

    breaches: list[str] = []
    gated = 0

    for name in sorted(scores):
        value = scores[name]
        floor = policy.quality_floor(name)
        if floor is None:
            print(f"  {name:<22} {value:.3f}   (measured, not gated)")
            continue
        gated += 1
        decision = policy.check_quality(name, value)
        marker = "FAIL" if decision.denied else "pass"
        print(f"  {name:<22} {value:.3f}   floor {floor:.2f}   [{marker}]")
        if decision.denied:
            breaches.append(decision.reason)

    print("-" * 68)
    if not gated:
        # Silence here would mean "everything passed"; it actually means
        # nothing was checked, which is a configuration error.
        print("FAILED: no gated metric was present in the results.")
        return 1

    if breaches:
        print(f"{'WARNING' if args.warn_only else 'FAILED'}: {len(breaches)} metric(s) below policy floor")
        for breach in breaches:
            print(f"  - {breach}")
        return 0 if args.warn_only else 1

    print(f"PASSED: all {gated} gated metric(s) meet the policy floors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
