"""Statistics on the saved evaluation results: confidence intervals, paired tests, latency percentiles.

Run: python -m evaluation.eval_statistics
Reads evaluation/results/*.json (no model is run) and writes evaluation/results/statistics.json.
Why: with few questions a score is uncertain (about +-1/sqrt(n)), and two systems that differ by one question
cannot be told apart. The paired test looks only at the questions where the two systems disagree.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.stats import mcnemar_exact, paired_counts, percentile, wilson_interval
from nexora.config import load_config

TARGET_ACCURACY = 0.90


def _load(results: Path, name: str) -> dict[str, Any] | None:
    path = results / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _share(k: int, n: int) -> dict[str, Any]:
    low, high = wilson_interval(k, n)
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "ci95": [round(low, 4), round(high, 4)]}


def _passed(data: dict[str, Any], system: str) -> dict[int, bool]:
    """Per question: did ``system`` ('a' or 'b') pass in the first run?"""
    return {q["id"]: bool(q[system][0]["passed"]) for q in data["questions"]}


def _paired(name_a: str, a: dict[int, bool], name_b: str, b: dict[int, bool]) -> dict[str, Any]:
    only_a, only_b = paired_counts(a, b)
    return {"a": name_a, "b": name_b, "only_a_right": only_a, "only_b_right": only_b,
            "p_value": round(mcnemar_exact(only_a, only_b), 4),
            "conclusive": mcnemar_exact(only_a, only_b) < 0.05}


def compute(results: Path) -> dict[str, Any]:
    """Every statistic that can be computed from the saved result files."""
    out: dict[str, Any] = {"run_at": datetime.now(UTC).isoformat(timespec="seconds"),
                           "target_accuracy": TARGET_ACCURACY}
    rules = _load(results, "rules_eval.json")
    semantic = _load(results, "rules_eval_semantic_only.json")
    if rules:
        nexora, baseline = _passed(rules, "a"), _passed(rules, "b")
        systems = {"nexora": nexora, "baseline_no_documents": baseline}
        if semantic:
            systems["nexora_semantic_only"] = _passed(semantic, "a")
        out["answers"] = {name: _share(sum(v.values()), len(v)) for name, v in systems.items()}
        low = out["answers"]["nexora"]["ci95"][0]
        out["target_check"] = {"target": TARGET_ACCURACY, "ci_lower_bound": low,
                               "proven": low > TARGET_ACCURACY,
                               "note": "the target is proven only if the whole 95% interval is above it"}
        out["paired"] = [_paired("nexora", nexora, "baseline_no_documents", baseline)]
        if semantic:
            out["paired"].append(_paired("nexora", nexora, "nexora_semantic_only", systems["nexora_semantic_only"]))
        latencies = [r["latency_seconds"] for q in rules["questions"] for r in q["a"]]
        out["latency_seconds"] = {"n": len(latencies), "p50": round(percentile(latencies, 50), 2),
                                  "p95": round(percentile(latencies, 95), 2), "max": round(max(latencies), 2),
                                  "note": "the first call after start-up loads the models, so p95/max include warm-up"}
    retrieval = _load(results, "retrieval_dev_eval.json")
    if retrieval:
        n = retrieval["n_answerable"]
        out["retrieval_hit_at_1"] = {mode: _share(round(m["hit@1"] * n), n) for mode, m in retrieval["modes"].items()}
    detection = _load(results, "detection_eval.json")
    if detection:
        found = sum(s["errors_found"] for s in detection["seeds"])
        total = sum(s["errors_inserted"] for s in detection["seeds"])
        out["detection_recall_all_seeds"] = _share(found, total)
        out["detection_note"] = ("the three seeds are separate datasets, but the detectors were built on "
                                 "the same generator")
    return out


def main() -> int:
    """CLI entry point."""
    results = load_config().path("eval_results")
    stats = compute(results)
    if "answers" not in stats:
        print("Run `python -m evaluation.eval_rules` first.", file=sys.stderr)
        return 1
    (results / "statistics.json").write_text(json.dumps(stats, indent=2), encoding="utf-8")
    for name, s in stats["answers"].items():
        print(f"{name:24} {s['k']}/{s['n']} = {s['share']:.1%}  95% CI {s['ci95'][0]:.1%} to {s['ci95'][1]:.1%}")
    for p in stats["paired"]:
        print(f"{p['a']} vs {p['b']}: only first right {p['only_a_right']}, only second right {p['only_b_right']}, "
              f"p = {p['p_value']} ({'conclusive' if p['conclusive'] else 'not conclusive'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
