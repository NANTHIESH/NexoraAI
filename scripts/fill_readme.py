"""Fill the README result tables from evaluation/results/*.json (no number is typed by hand).

Run: python -m scripts.fill_readme
Replaces the text between `<!-- BEGIN:name -->` and `<!-- END:name -->` markers in README.md.
A table whose JSON file does not exist says "not run yet".
"""

from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

from nexora.config import PROJECT_ROOT, load_config

NOT_RUN = "_not run yet_ (run `make eval`)"
README = PROJECT_ROOT / "README.md"


def _load(results: Path, name: str) -> dict[str, Any] | None:
    path = results / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _mean(runs: list[dict[str, Any]], key: str) -> float | None:
    values = [r[key] for r in runs if r.get(key) is not None]
    return statistics.mean(values) if values else None


def _table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    return "\n".join(lines + ["| " + " | ".join(r) + " |" for r in rows])


def render_rules(results: Path) -> str:
    """Rules assistant vs. baseline (and the semantic-only variant if it was run)."""
    data = _load(results, "rules_eval.json")
    if data is None:
        return NOT_RUN
    systems = [("Nexora (retrieval + refusal)", data["system_a"]["runs"]),
               ("Baseline: same model, no documents", data["system_b"]["runs"])]
    older = _load(results, "rules_eval_semantic_only.json")
    if older:
        systems.insert(1, ("Nexora with semantic-only retrieval (before Phase 6)", older["system_a"]["runs"]))
    rows = []
    for name, runs in systems:
        first = runs[0]
        rows.append([name, f"{first['passed']}/{first['questions']}", _pct(_mean(runs, "accuracy")),
                     _pct(_mean(runs, "citation_accuracy")), _pct(_mean(runs, "refusal_accuracy")),
                     str(first["hallucinations"]), f"{_mean(runs, 'latency_mean_seconds'):.1f} s"])
    a = data["accuracy_across_runs"]["A"]
    verdict = "met" if a["mean"] > 0.90 else "NOT met"
    text = (f"Model `{data['model']}`, temperature {data['settings']['temperature']}, {data['runs']} run(s), "
            f"run on {data['run_at'][:10]}. Passed / accuracy are from the first run and the mean over runs.\n\n"
            + _table(["System", "Passed (run 1)", "Accuracy", "Citation accuracy", "Refusal accuracy",
                      "Hallucinations", "Mean latency"], rows)
            + f"\n\nTarget: accuracy above 90% (at least 14 of 15) for Nexora: **{verdict}** "
              f"(mean {a['mean']:.1%}, standard deviation {a['stdev']:.3f} over {data['runs']} run(s)).")
    per_q = [[str(q["id"]), q["question"], "yes" if q["a"][0]["passed"] else "**no**",
              "yes" if q["b"][0]["passed"] else "**no**"] for q in data["questions"]]
    return text + "\n\n" + _table(["#", "Question", "Nexora passed", "Baseline passed"], per_q)


def render_retrieval(results: Path) -> str:
    """Retrieval modes on the development questions."""
    data = _load(results, "retrieval_dev_eval.json")
    if data is None:
        return NOT_RUN
    rows = [[name, f"{m['hit@1']:.2f}", f"{m['hit@3']:.2f}", f"{m['mrr']:.2f}",
             f"{m['unanswerable_refused_by_threshold']:.0%}"] for name, m in data["modes"].items()]
    return (f"{data['n_answerable']} answerable and {data['n_unanswerable']} unanswerable development questions "
            "(not the 15 evaluation questions); retrieval only, no language model.\n\n"
            + _table(["Mode", "Right document first", "In top 3", "MRR", "Unanswerable refused by threshold"], rows))


def render_detection(results: Path) -> str:
    """Detection recall, false alerts and savings per seed."""
    data = _load(results, "detection_eval.json")
    if data is None:
        return NOT_RUN
    rows = [[str(s["seed"]), str(s["errors_inserted"]), str(s["errors_found"]), f"{s['recall']:.0%}",
             str(s["false_alerts"]), _pct(s["precision"]), str(len(s["decoys_wrongly_flagged"])),
             f"{s['estimated_savings_eur']['monthly']:,.0f}"] for s in data["seeds"]]
    types: dict[str, list[int]] = {}
    for s in data["seeds"]:
        for kind, v in s["per_type"].items():
            totals = types.setdefault(kind, [0, 0])
            totals[0] += v["found"]
            totals[1] += v["inserted"]
    by_type = [[k, f"{f}/{n}", f"{f / n:.0%}"] for k, (f, n) in types.items()]
    t = data["targets"]
    ok = all(s["meets_targets"] for s in data["seeds"])
    return (_table(["Seed", "Errors inserted", "Found", "Recall", "False alerts", "Precision",
                    "Decoys wrongly flagged", "Estimated savings (EUR/month)"], rows)
            + f"\n\nTargets: recall >= {t['recall']:.0%}, false alerts <= {t['max_false_alerts']}, precision >= "
              f"{t['precision']:.0%}: **{'met on all seeds' if ok else 'NOT met on every seed'}**. "
              "Thresholds are the `config.yaml` defaults for every seed. The generator and the detectors were "
              "built together on synthetic data, so this shows consistency, not performance on real bank data.\n\n"
            + _table(["Error type", "Found (all seeds)", "Recall"], by_type))


def render_time(results: Path) -> str:
    """Manual vs. Nexora task times."""
    data = _load(results, "time_eval.json")
    if data is None:
        return NOT_RUN
    rows = []
    for name, task in data["tasks"].items():
        manual, nexora, red = task["manual_seconds"], task["nexora_seconds"], task["reduction"]
        rows.append([name, "not measured" if manual is None else f"{manual:.0f} s",
                     "not measured" if nexora is None else f"{nexora:.0f} s",
                     "not measured" if red is None else f"{red:.0%}"])
    return (_table(["Task", "Manual (stopwatch)", "With Nexora (stopwatch)", "Time saved"], rows)
            + f"\n\nTarget: {data['target_time_reduction']:.0%} less time. Values are entered by the team with "
              "`python -m evaluation.eval_time record ...`; nothing is estimated.")


def render_satisfaction(results: Path) -> str:
    """User satisfaction from thumbs up/down."""
    data = _load(results, "satisfaction.json")
    if data is None:
        return NOT_RUN
    if data["satisfaction"] is None:
        return f"**not measured** (no thumbs up/down feedback yet; target {data['target']:.0%})."
    return (f"{data['thumbs_up']} of {data['feedback_count']} ratings were positive: "
            f"**{data['satisfaction']:.1%}** (target {data['target']:.0%}, {data['status']}).")


RENDERERS = {"rules": render_rules, "retrieval": render_retrieval, "detection": render_detection,
             "time": render_time, "satisfaction": render_satisfaction}


def fill(readme_text: str, results: Path) -> str:
    """Return ``readme_text`` with every marked block regenerated."""
    for name, renderer in RENDERERS.items():
        pattern = re.compile(rf"(<!-- BEGIN:{name} -->)(.*?)(<!-- END:{name} -->)", re.DOTALL)
        if not pattern.search(readme_text):
            raise ValueError(f"README.md has no <!-- BEGIN:{name} --> ... <!-- END:{name} --> block")
        readme_text = pattern.sub(lambda m, r=renderer: f"{m.group(1)}\n{r(results)}\n{m.group(3)}", readme_text)
    return readme_text


def main() -> int:
    """CLI entry point."""
    results = load_config().path("eval_results")
    try:
        README.write_text(fill(README.read_text(encoding="utf-8"), results), encoding="utf-8", newline="\n")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Updated result tables in {README}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
