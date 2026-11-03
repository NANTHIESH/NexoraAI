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


def _ci(share: dict[str, Any]) -> str:
    low, high = share["ci95"]
    return f"{share['k']}/{share['n']} = {share['share']:.1%} ({low:.0%} to {high:.0%})"


def render_statistics(results: Path) -> str:
    """Confidence intervals and paired tests on the answer evaluation."""
    data = _load(results, "statistics.json")
    if data is None:
        return NOT_RUN
    names = {"nexora": "Nexora (hybrid + reranker)", "nexora_semantic_only": "Nexora, semantic-only retrieval",
             "baseline_no_documents": "Baseline: same model, no documents"}
    rows = [[names.get(k, k), _ci(v)] for k, v in data["answers"].items()]
    paired = [[f"{names.get(p['a'], p['a'])} vs {names.get(p['b'], p['b'])}", str(p["only_a_right"]),
               str(p["only_b_right"]), f"{p['p_value']:.3f}",
               "yes" if p["conclusive"] else "**no**"] for p in data["paired"]]
    check = data["target_check"]
    lat = data["latency_seconds"]
    verdict = "proven" if check["proven"] else "not proven"
    text = (
        "Accuracy on the 15 evaluation questions with a 95% Wilson interval "
        "(the plausible range of the true score).\n\n"
        + _table(["System", "Right / questions = accuracy (95% interval)"], rows)
        + f"\n\nThe 90% target is **{verdict}**: the interval's lower end is {check['ci_lower_bound']:.0%}. "
          "With 15 questions only a large gap can be shown.\n\n"
        + "Paired comparison on the same questions (exact McNemar test; only questions where the two systems "
          "disagree count):\n\n"
        + _table(["Comparison", "Only first right", "Only second right", "p-value", "Conclusive (p < 0.05)"], paired)
        + f"\n\nLatency of answers (Nexora, all runs): median {lat['p50']} s, 95th percentile {lat['p95']} s "
          "(includes the slow first call that loads the models).")
    if "detection_recall_all_seeds" in data:
        text += "\n\nDetection recall over all seeds: " + _ci(data["detection_recall_all_seeds"]) + "."
    return text


def render_understanding(results: Path) -> str:
    """Evaluation D: model-based understanding vs. keyword routing."""
    data = _load(results, "understanding_eval.json")
    if data is None:
        return NOT_RUN
    acc = data["intent_accuracy"]
    rows = [["Keyword router (rules only)", _ci(acc["keywords"])], ["Model (llama)", _ci(acc["model"])],
            ["Model as deployed (plus the exact-lookup rules)", _ci(acc["model_as_deployed"])]]
    tags = sorted(data["by_tag"]["model"])
    by_tag = [[t, _ci(data["by_tag"]["keywords"][t]), _ci(data["by_tag"]["model"][t])] for t in tags]
    p = data["paired_model_vs_keywords"]
    lat = data["latency_seconds"]
    return (
        f"Model `{data['model']}`, {data['n']} labelled messages (typos, slang, follow-ups, vague messages), run on "
        f"{data['run_at'][:10]}. Labels are a judgement call by the team; the messages are not in the prompt's "
        "few-shot examples.\n\n"
        + _table(["Intent classification", "Right / messages = accuracy (95% interval)"], rows)
        + f"\n\nPaired test, model vs keywords: the model alone was right on {p['only_model_right']} messages where "
          f"the keywords were wrong and wrong on {p['only_keywords_right']} where they were right "
          f"(p = {p['p_value']:.4f}).\n\n"
        + _table(["Kind of message", "Keywords", "Model"], by_tag)
        + f"\n\nClarifying questions asked on clear messages: {_ci(data['unneeded_clarifications'])} "
          "(lower is better). "
          f"Vague messages that got a clarifying question: {_ci(data['vague_messages_clarified'])}. "
          f"Rewritten questions that kept every required name and number: "
          f"{_ci(data['rewrite_keeps_names_and_numbers'])}. Model latency: median {lat['p50']} s, "
          f"95th percentile {lat['p95']} s.")


def render_data_answers(results: Path) -> str:
    """Evaluation E: computed answers against an independent ground truth."""
    data = _load(results, "data_answers_eval.json")
    if data is None:
        return NOT_RUN
    rows = [[name.capitalize(), _ci(v)] for name, v in data["by_type"].items()]
    nc = data["negative_control"]
    return (f"Seeds {', '.join(map(str, data['seeds']))}; ground truth computed with plain pandas on the raw CSV "
            "files, language model switched off. An answer is right only if it contains every expected number.\n\n"
            + _table(["Question type", "Right / questions = accuracy (95% interval)"], rows)
            + f"\n\nOverall: **{_ci(data['overall'])}**. Negative control: {nc['k']} of {nc['n']} answers also "
              "satisfy the expectations of a different question (the check is not lenient).")


def render_safety(results: Path) -> str:
    """Evaluation F: attack success rate per group."""
    data = _load(results, "safety_eval.json")
    if data is None:
        return NOT_RUN
    rows = [[name, _ci(v)] for name, v in data["groups"].items()]
    findings = [f"- **{a['group']}**: {a['attack']}" for a in data["successful_attacks"]
                if "paraphrased to avoid" not in a["group"]]
    paraphrased = sum(1 for a in data["successful_attacks"] if "paraphrased to avoid" in a["group"])
    note = data["note"][:1].upper() + data["note"][1:]
    text = ("Each row is the share of attacks that **succeeded** (0% is the goal). " + note + ".\n\n"
            + _table(["Attack group", "Attacks that succeeded (95% interval)"], rows))
    if findings or paraphrased:
        text += "\n\nAttacks that got through (findings, not hidden):\n\n" + "\n".join(findings)
        if paraphrased:
            text += (f"\n- **{paraphrased} paraphrased injections** passed the document guard, which only knows "
                     "listed phrases.")
    return text


def render_faithfulness(results: Path) -> str:
    """Evaluation G: faithfulness, abstention and confidence of the document answers."""
    data = _load(results, "faithfulness_eval.json")
    if data is None:
        return NOT_RUN
    labels = {"refused_when_answerable": "Refused although the documents answer it (lower is better)",
              "refused_when_unanswerable": "Refused when the documents do not answer it (higher is better)",
              "answerable_answered_with_right_document": "Answerable questions answered, citing the right document",
              "answers_with_figures_all_supported": "Answers whose figures all appear in the retrieved passages",
              "cited_passage_contains_the_figures": "Answers whose cited passage contains their figures"}
    rows = [[text, *(_ci(data[s][key]) if data[s][key]["n"] else "n/a" for s in ("evaluation_set",
            "development_set", "all"))] for key, text in labels.items()]
    bands = [[f"{b['score_from']:.2f} to {b['score_to']:.2f}", _ci(b["right"])]
             for b in data["accuracy_by_retrieval_score"] if b["right"]["n"]]
    unsupported = data["unsupported_figures"]
    return (f"Model `{data['model']}`; {data['all']['questions']} questions answered by the real assistant. "
            f"{data['note'].capitalize()}.\n\n"
            + _table(["Measure", "Evaluation set (15)", "Development set (38)", "All"], rows)
            + "\n\nDoes the retrieval score tell right answers from wrong ones? Share of answerable questions "
              "answered with the right document, by score of the best passage:\n\n"
            + _table(["Retrieval score", "Right / questions (95% interval)"], bands)
            + ("\n\nFigures not found in the sources: none." if not unsupported else
               "\n\nFigures not found in the sources: " + "; ".join(f"{u['figures']} in \"{u['answer'][:80]}...\""
                                                                    for u in unsupported) + "."))


def render_production(results: Path) -> str:
    """Evaluation H: behaviour in real use, from the logs."""
    data = _load(results, "production_metrics.json")
    if data is None:
        return NOT_RUN
    rows = [["Answered", _ci(data["answered"])], ["No source found (refused)", _ci(data["refused_no_source_found"])],
            ["Denied by role", _ci(data["denied_by_role"])],
            ["Replies that were a clarifying question", _ci(data["clarifying_questions"])],
            ["Answers in extractive mode (no model)", _ci(data["answers_in_extractive_mode"])],
            ["Questions routed by keywords only", _ci(data["routed_by_keywords_only"])],
            ["Failed logins", _ci(data["login_failures"])]]
    fb = data["feedback"]
    return (f"{data['questions_asked']} questions from {data['users']} users in {data['conversations']} "
            f"conversations. {data['note'].capitalize()}.\n\n" + _table(["Measure", "Share (95% interval)"], rows)
            + f"\n\nFeedback: {fb['thumbs_up']} thumbs up, {fb['thumbs_down']} thumbs down. "
              f"Recommendations confirmed: {data['confirmed_recommendations']}. Registrations: "
              f"{data['registrations']}. Latency is not logged yet.")


def render_model_comparison(results: Path) -> str:
    """Evaluation I: several language models on the same questions."""
    data = _load(results, "model_comparison.json")
    if data is None:
        return NOT_RUN
    models = list(data["models"])

    def cell(share: dict[str, Any] | None) -> str:
        return "n/a" if not share or not share["n"] else f"{share['k']}/{share['n']} ({share['share']:.0%})"

    def get(model: str, *path: str) -> Any:
        node: Any = data["models"][model]
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        return node

    rows = [
        ["Rules: questions passed (of 15)"] + [cell(get(m, "rules", "passed")) for m in models],
        ["Rules: same model without documents"]
        + [str(get(m, "rules", "baseline_no_documents_passed")) for m in models],
        ["Documents: answerable questions wrongly refused"]
        + [cell(get(m, "documents", "refused_when_answerable")) for m in models],
        ["Documents: right document cited"] + [cell(get(m, "documents", "right_document")) for m in models],
        ["Documents: unanswerable questions refused"]
        + [cell(get(m, "documents", "refused_when_unanswerable")) for m in models],
        ["Documents: figures not found in the sources"]
        + [str(get(m, "documents", "unsupported_figures")) for m in models],
        ["Documents: answers that paste the source text instead of answering"]
        + [cell(get(m, "documents", "repeats_source_block")) for m in models],
        ["Understanding: accuracy as deployed (42 messages)"]
        + [cell(get(m, "understanding", "intent_accuracy_as_deployed")) for m in models],
        ["Understanding: needless clarifying questions"]
        + [cell(get(m, "understanding", "needless_clarifications")) for m in models],
        ["Understanding: vague messages that got a question"]
        + [cell(get(m, "understanding", "vague_clarified")) for m in models],
        ["Answer latency, median (s)"] + [str(get(m, "rules", "latency_median_seconds")) for m in models],
        ["Understanding latency, median (s)"]
        + [str(get(m, "understanding", "latency_median_seconds")) for m in models],
    ]
    task_names = {"rules": "Rules questions (15)", "documents": "Right document cited (40)",
                  "answered": "Answerable question answered, not refused (40)",
                  "understanding": "Understanding messages (42)"}
    paired = [[p["model"], task_names.get(p["task"], p["task"]), str(p["only_model_right"]),
               str(p["only_reference_right"]), f"{p['p_value']:.3f}",
               "**real difference**" if p["conclusive"] else "not conclusive"]
              for p in data["paired_vs_reference"]]
    return (f"Reference model: `{data['reference']}`. {data['note'].capitalize()}. Run on {data['run_at'][:10]}.\n\n"
            + _table(["Measure"] + [f"`{m}`" for m in models], rows)
            + "\n\nPaired comparison with the reference model on the same questions (exact McNemar test; a gap "
              "is only real if p < 0.05):\n\n"
            + _table(["Model", "Task", "Only this model right", "Only reference right", "p-value", "Verdict"], paired))


def render_retrieval_comparison(results: Path) -> str:
    """Evaluation J: embedding model, reranker and query expansion compared on held-out questions."""
    data = _load(results, "retrieval_comparison.json")
    if data is None:
        return NOT_RUN
    rows = []
    for name, v in data["setups"].items():
        tp, dv, r = v["heldout_passage"], v["development_document"], v["refusal"]
        low, high = tp["hit@1_ci95"]
        rows.append([name, f"{tp['hit@1']:.0%} ({low:.0%} to {high:.0%})", f"{tp['hit@3']:.0%}", f"{tp['mrr']:.2f}",
                     f"{dv['hit@1']:.0%}", f"{r['auc_heldout']:.2f}", f"{r['threshold_chosen_on_development']:.2f}",
                     f"{v['seconds_per_query'] * 1000:.0f}"])
    held_out = next(iter(data["setups"].values()))["heldout_passage"]["n"]
    return (f"Held-out questions: {held_out} answerable ones with the "
            "exact right passage known, written before any change was tried and never used to choose settings. "
            "Development questions (right document known) were used to choose settings. "
            f"Query expansion = {data['expansion_queries']} rewordings per question from `{data['expansion_model']}` "
            "(its time, about 0.3 to 0.5 s per question, is not in the last column).\n\n"
            + _table(["Setup", "Held-out: right passage first (95% interval)", "In top 3", "MRR", "Development: right "
                      "document first", "Answerable vs unanswerable (AUC)", "Refusal threshold (from development)",
                      "Search time (ms)"], rows))


RENDERERS = {"rules": render_rules, "retrieval": render_retrieval, "detection": render_detection,
             "time": render_time, "satisfaction": render_satisfaction, "statistics": render_statistics,
             "understanding": render_understanding, "data_answers": render_data_answers,
             "safety": render_safety, "faithfulness": render_faithfulness,
             "production": render_production,
             "model_comparison": render_model_comparison, "retrieval_comparison": render_retrieval_comparison}


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
