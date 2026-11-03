"""Evaluation I: compare language models on the same questions.

Run: python -m evaluation.compare_models [--models a b c] [--skip-run]
For every model the three model-dependent evaluations run with identical settings (same questions, prompts,
retrieval, temperature 0, seed 42); only the model changes:
  A. rules assistant on the 15 evaluation questions       (evaluation/eval_rules.py)
  D. understanding messages, 42 labelled messages         (evaluation/eval_understanding.py)
  G. faithfulness and refusals on 53 document questions   (evaluation/eval_faithfulness.py)
Each model's results are stored under its own file name (suffix __<model>), so nothing is overwritten.
Every other model is compared with the reference model (the first in --models) question by question with an
exact McNemar test: a gap is only called real if p < 0.05. Latency is the median time per answer; the first
call after a model loads is slow and does not move the median. qwen3 models run with thinking switched off,
so every model is judged on its plain answer.
Writes evaluation/results/model_comparison.json. --skip-run only compares results that already exist.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.stats import mcnemar_exact, paired_counts, wilson_interval
from nexora.config import load_config

DEFAULT_MODELS = ["llama3.2:3b", "llama3.2:1b", "qwen3:1.7b", "qwen3:8b"]
STEPS = (("evaluation.eval_rules", "rules_eval"), ("evaluation.eval_understanding", "understanding_eval"),
         ("evaluation.eval_faithfulness", "faithfulness_eval"))


def suffix(model: str) -> str:
    """File-name suffix for a model, e.g. ``qwen3:8b`` -> ``__qwen3-8b``."""
    return "__" + re.sub(r"[^a-z0-9.]+", "-", model.lower()).strip("-")


def run_model(model: str) -> float:
    """Run the three evaluations for ``model``; returns the wall-clock seconds it took."""
    start = time.perf_counter()
    for module, _ in STEPS:
        print(f"\n=== {model}: {module}", flush=True)
        done = subprocess.run([sys.executable, "-m", module, "--model", model, "--suffix", suffix(model)],
                              check=False)
        if done.returncode != 0:
            print(f"!! {module} failed for {model} (exit code {done.returncode})", flush=True)
    return time.perf_counter() - start


def _load(results: Path, base: str, model: str) -> dict[str, Any] | None:
    path = results / f"{base}{suffix(model)}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _share(k: int, n: int) -> dict[str, Any]:
    low, high = wilson_interval(k, n)
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "ci95": [round(low, 4), round(high, 4)]}


def summarise_model(results: Path, model: str) -> dict[str, Any]:
    """Headline numbers and per-question outcomes of one model (``None`` entries where a result is missing)."""
    rules, understanding, faith = (_load(results, base, model) for _, base in STEPS)
    out: dict[str, Any] = {"model": model, "per_question": {}}
    if rules:
        a = rules["system_a"]["runs"][0]
        b = rules["system_b"]["runs"][0]
        answers = [q["a"][0] for q in rules["questions"]]
        out["rules"] = {"passed": _share(a["passed"], a["questions"]), "baseline_no_documents_passed": b["passed"],
                        "citation_accuracy": a["citation_accuracy"], "refusal_accuracy": a["refusal_accuracy"],
                        "hallucinations": a["hallucinations"],
                        "latency_median_seconds": round(statistics.median(x["latency_seconds"] for x in answers), 2)}
        out["per_question"]["rules"] = {q["question"]: bool(q["a"][0]["passed"]) for q in rules["questions"]}
    if faith:
        every = faith["all"]
        out["documents"] = {
            "refused_when_answerable": every["refused_when_answerable"],
            "right_document": every["answerable_answered_with_right_document"],
            "refused_when_unanswerable": every["refused_when_unanswerable"],
            "unsupported_figures": len(faith["unsupported_figures"]),
            "repeats_source_block": every.get("answers_that_repeat_the_source_block")}
        out["per_question"]["documents"] = {q["question"]: bool(q["right_document"]) for q in faith["questions"]
                                            if q["answerable"]}
        out["per_question"]["answered"] = {q["question"]: not q["refused"] for q in faith["questions"]
                                           if q["answerable"]}
    if understanding:
        out["understanding"] = {
            "intent_accuracy_as_deployed": understanding["intent_accuracy"]["model_as_deployed"],
            "intent_accuracy_model_alone": understanding["intent_accuracy"]["model"],
            "needless_clarifications": understanding["unneeded_clarifications"],
            "vague_clarified": understanding["vague_messages_clarified"],
            "unusable_answers": understanding["model_unusable_answers"],
            "latency_median_seconds": understanding["latency_seconds"]["p50"]}
        out["per_question"]["understanding"] = {i["text"]: bool(i["deployed_correct"]) for i in understanding["items"]}
    return out


def compare(results: Path, models: list[str], seconds: dict[str, float]) -> dict[str, Any]:
    """Summaries of every model plus paired tests against the reference (the first model)."""
    summaries = {m: summarise_model(results, m) for m in models}
    reference = summaries[models[0]]
    paired = []
    for m in models[1:]:
        for task in ("rules", "documents", "answered", "understanding"):
            a, b = summaries[m]["per_question"].get(task), reference["per_question"].get(task)
            if not a or not b:
                continue
            only_model, only_reference = paired_counts(a, b)
            p = mcnemar_exact(only_model, only_reference)
            paired.append({"model": m, "reference": models[0], "task": task, "only_model_right": only_model,
                           "only_reference_right": only_reference, "p_value": round(p, 4), "conclusive": p < 0.05})
    for s in summaries.values():
        s.pop("per_question")
    return {"run_at": datetime.now(UTC).isoformat(timespec="seconds"), "reference": models[0], "models": summaries,
            "paired_vs_reference": paired, "wall_clock_minutes": {m: round(t / 60, 1) for m, t in seconds.items()},
            "note": "same questions, prompts, retrieval, temperature 0 and seed for every model; qwen3 thinking off"}


def _cell(share: dict[str, Any] | None) -> str:
    return "n/a" if not share or not share["n"] else f"{share['k']}/{share['n']} ({share['share']:.0%})"


def print_table(result: dict[str, Any]) -> None:
    """A readable summary of the comparison."""
    models = list(result["models"])
    rows = [("Rules: questions passed (of 15)", lambda s: _cell(s.get("rules", {}).get("passed"))),
            ("Rules: baseline without documents", lambda s: str(s.get("rules", {}).get("baseline_no_documents_passed", "n/a"))),
            ("Documents: wrongly refused", lambda s: _cell(s.get("documents", {}).get("refused_when_answerable"))),
            ("Documents: right document cited", lambda s: _cell(s.get("documents", {}).get("right_document"))),
            ("Documents: unanswerable refused", lambda s: _cell(s.get("documents", {}).get("refused_when_unanswerable"))),
            ("Documents: figures not in sources", lambda s: str(s.get("documents", {}).get("unsupported_figures", "n/a"))),
            ("Documents: pasted the source text", lambda s: _cell(s.get("documents", {}).get("repeats_source_block"))),
            ("Understanding: accuracy as deployed",
             lambda s: _cell(s.get("understanding", {}).get("intent_accuracy_as_deployed"))),
            ("Understanding: needless questions", lambda s: _cell(s.get("understanding", {}).get("needless_clarifications"))),
            ("Understanding: vague messages asked", lambda s: _cell(s.get("understanding", {}).get("vague_clarified"))),
            ("Answer latency, median (s)", lambda s: str(s.get("rules", {}).get("latency_median_seconds", "n/a"))),
            ("Understanding latency, median (s)",
             lambda s: str(s.get("understanding", {}).get("latency_median_seconds", "n/a")))]
    width = max(len(r[0]) for r in rows) + 2
    print("".ljust(width) + "".join(m.ljust(16) for m in models))
    for name, getter in rows:
        print(name.ljust(width) + "".join(getter(result["models"][m]).ljust(16) for m in models))
    print("\nPaired tests against the reference model (exact McNemar):")
    for p in result["paired_vs_reference"]:
        print(f"  {p['model']:14} {p['task']:14} only this model right {p['only_model_right']}, only {p['reference']} "
              f"right {p['only_reference_right']}, p = {p['p_value']:.3f} "
              f"({'REAL difference' if p['conclusive'] else 'not conclusive'})")


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Compare language models on the same evaluations")
    parser.add_argument("--models", nargs="+", default=DEFAULT_MODELS, help="reference model first")
    parser.add_argument("--skip-run", action="store_true", help="only compare results that already exist")
    args = parser.parse_args()
    results = load_config().path("eval_results")
    seconds: dict[str, float] = {}
    if not args.skip_run:
        for model in args.models:
            seconds[model] = run_model(model)
    result = compare(results, args.models, seconds)
    (results / "model_comparison.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    print_table(result)
    print(f"\nwritten to {results / 'model_comparison.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
