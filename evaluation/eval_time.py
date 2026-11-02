"""Evaluation C: manual vs. Nexora task times, recorded from REAL stopwatch runs by the team.

Usage:
  python -m evaluation.eval_time init                       # create the template (all values null)
  python -m evaluation.eval_time record --task find_rule --mode manual --seconds 240 --by Ameni
  python -m evaluation.eval_time record --task find_rule --mode nexora --seconds 20 --by Ameni
  python -m evaluation.eval_time summary                    # reduction per task, once both are entered

Nothing is ever filled in automatically: until the team enters measurements the file keeps null.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from nexora.config import load_config

TASKS = {
    "find_rule": "Find a company rule (for example: how many vacation days carry over)",
    "monthly_report": "Produce a monthly report (income, expenses, net, task status)",
    "wasteful_subscriptions": "Find wasteful software subscriptions",
}
TARGET_REDUCTION = 0.50


def template() -> dict[str, Any]:
    """Empty results file: every measurement is null."""
    return {"target_time_reduction": TARGET_REDUCTION,
            "note": "Values come from real stopwatch runs entered by the team. null = not measured yet.",
            "tasks": {name: {"description": text, "manual_seconds": None, "nexora_seconds": None,
                             "reduction": None, "measurements": []} for name, text in TASKS.items()}}


def results_path() -> Path:
    return load_config().path("eval_results") / "time_eval.json"


def load(path: Path | None = None) -> dict[str, Any]:
    """Read the results file, or the empty template if it does not exist."""
    path = path or results_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else template()


def save(data: dict[str, Any], path: Path | None = None) -> Path:
    """Write the results file."""
    path = path or results_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def record(data: dict[str, Any], task: str, mode: str, seconds: float, by: str,
           when: str | None = None) -> None:
    """Add one stopwatch measurement (``mode`` is ``manual`` or ``nexora``)."""
    if task not in TASKS or mode not in ("manual", "nexora") or seconds <= 0:
        raise ValueError(f"task must be one of {list(TASKS)}, mode manual|nexora, seconds > 0")
    data["tasks"][task]["measurements"].append(
        {"mode": mode, "seconds": seconds, "measured_by": by, "date": when or date.today().isoformat()})
    summary = summarize(data)[task]  # keep the headline fields in step with the measurements
    data["tasks"][task].update(manual_seconds=summary["manual_seconds"], nexora_seconds=summary["nexora_seconds"],
                               reduction=summary["reduction"])


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    """Per task: average manual and Nexora seconds and the reduction (None until both exist)."""
    summary: dict[str, Any] = {}
    for name, task in data["tasks"].items():
        by_mode = {m: [x["seconds"] for x in task["measurements"] if x["mode"] == m]
                   for m in ("manual", "nexora")}
        avg = {m: (sum(v) / len(v) if v else None) for m, v in by_mode.items()}
        reduction = (1 - avg["nexora"] / avg["manual"]) if avg["manual"] and avg["nexora"] else None
        summary[name] = {"manual_seconds": avg["manual"], "nexora_seconds": avg["nexora"],
                         "runs_manual": len(by_mode["manual"]), "runs_nexora": len(by_mode["nexora"]),
                         "reduction": round(reduction, 3) if reduction is not None else None,
                         "meets_target": None if reduction is None else reduction >= TARGET_REDUCTION}
    return summary


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    rec = sub.add_parser("record")
    rec.add_argument("--task", required=True, choices=list(TASKS))
    rec.add_argument("--mode", required=True, choices=["manual", "nexora"])
    rec.add_argument("--seconds", required=True, type=float)
    rec.add_argument("--by", required=True, help="who held the stopwatch")
    sub.add_parser("summary")
    args = parser.parse_args()
    data = load()
    if args.command == "init":
        if results_path().exists():
            print(f"{results_path()} already exists; not overwritten.")
            return 0
        print(f"Created template {save(template())}")
    elif args.command == "record":
        record(data, args.task, args.mode, args.seconds, args.by)
        print(f"Saved {save(data)}")
    else:
        for name, s in summarize(data).items():
            print(f"{name:<24} manual={s['manual_seconds']} nexora={s['nexora_seconds']} "
                  f"reduction={s['reduction'] if s['reduction'] is not None else 'not measured'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
