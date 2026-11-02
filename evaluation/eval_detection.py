"""Evaluation B: detection recall and false alerts on the 20 planted errors, for several seeds.

Run: python -m evaluation.eval_detection [--seeds 42 7 123]
For every seed the data is generated into a temporary folder (data/generated is not touched),
all five detectors run with the thresholds from config.yaml (no per-seed tuning), and the flagged
detection keys are compared with ground_truth.json. Writes evaluation/results/detection_eval.json.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.generate_data import generate

from nexora.agents.subscription import analyze_subscriptions
from nexora.config import Config, load_config
from nexora.db.repositories import frames_from_csv

DEFAULT_SEEDS = (42, 7, 123)
TARGETS = {"recall": 0.80, "max_false_alerts": 3, "precision": 0.80}


def compare(truth: dict[str, Any], flagged: list[str]) -> dict[str, Any]:
    """Recall, precision, false alerts, per-type breakdown and wrongly flagged decoys."""
    errors = truth["errors"]
    planted = {e["expected_detection_key"]: e["type"] for e in errors}
    found = sorted(set(flagged) & set(planted))
    false_alerts = sorted(set(flagged) - set(planted))
    decoys_flagged = sorted(set(flagged) & {d["would_be_false_key"] for d in truth["decoys"]})
    inserted = Counter(e["type"] for e in errors)
    found_by_type = Counter(planted[k] for k in found)
    return {
        "errors_inserted": len(errors), "errors_found": len(found),
        "recall": round(len(found) / len(errors), 3),
        "flags_total": len(set(flagged)), "false_alerts": len(false_alerts),
        "false_alert_keys": false_alerts,
        "precision": round(len(found) / len(set(flagged)), 3) if flagged else None,
        "missed_keys": sorted(set(planted) - set(found)),
        "decoys_total": len(truth["decoys"]), "decoys_wrongly_flagged": decoys_flagged,
        "per_type": {t: {"inserted": inserted[t], "found": found_by_type[t],
                         "recall": round(found_by_type[t] / inserted[t], 3)} for t in sorted(inserted)},
    }


def evaluate_seed(seed: int, cfg: Config) -> dict[str, Any]:
    """Generate data for ``seed``, run the detectors, compare with the ground truth."""
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        generate(seed, folder)
        truth = json.loads((folder / "ground_truth.json").read_text(encoding="utf-8"))
        analysis = analyze_subscriptions(frames_from_csv(folder), cfg)
    result = compare(truth, [f.detection_key for f in analysis.flags])
    result.update(seed=seed, estimated_savings_eur={
        "monthly": analysis.monthly_savings, "yearly": analysis.yearly_savings,
        "one_off_refunds": analysis.one_off_recovery})
    result["meets_targets"] = (result["recall"] >= TARGETS["recall"]
                               and result["false_alerts"] <= TARGETS["max_false_alerts"]
                               and (result["precision"] or 0) >= TARGETS["precision"])
    return result


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    args = parser.parse_args()
    cfg = load_config()
    seeds = [evaluate_seed(s, cfg) for s in args.seeds]
    out = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "thresholds": dict(cfg.detectors), "targets": TARGETS, "seeds": seeds,
        "note": "Thresholds are the config.yaml defaults for every seed (no per-seed tuning). "
                "Generator and detectors were developed together on synthetic data.",
    }
    path = cfg.path("eval_results") / "detection_eval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{'seed':>5}{'inserted':>10}{'found':>7}{'recall':>8}{'false':>7}{'precision':>11}{'decoys hit':>12}")
    for s in seeds:
        print(f"{s['seed']:>5}{s['errors_inserted']:>10}{s['errors_found']:>7}{s['recall']:>8}"
              f"{s['false_alerts']:>7}{s['precision']:>11}{len(s['decoys_wrongly_flagged']):>12}")
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
