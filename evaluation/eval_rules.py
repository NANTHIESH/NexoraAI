"""Evaluation A: rules assistant (System A, with retrieval) vs. the same model without documents (System B).

Run: python -m evaluation.eval_rules [--runs N] [--model qwen2.5:7b] [--semantic-only]
Both systems use the same local model at temperature 0. Scoring (see docs/evaluation-protocol.md):
- answerable: passes if the answer contains every expected fact (and none of the forbidden texts);
  System A must also cite the correct document.
- unanswerable: System A passes if it refuses (refusal text or NOT_FOUND); System B passes if it
  explicitly says it does not know. Answering with content is a hallucination and a fail.
Writes evaluation/results/rules_eval.json (rules_eval_semantic_only.json with --semantic-only).
Needs Ollama running and the index built (`make index`).
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexora.config import PROJECT_ROOT, Config, load_config
from nexora.llm.ollama_client import OllamaClient
from nexora.rag.generator import Answer, Assistant
from nexora.rag.retriever import Retriever
from nexora.rag.vectorstore import EmptyIndexError

QA_PATH = PROJECT_ROOT / "evaluation" / "qa_set.json"
DONT_KNOW = re.compile(
    r"don'?t know|do not know|don'?t have (that |this |any |specific |access to )?(information|details|data)|"
    r"do not have (that |this |any |specific )?(information|details)|not aware|no (specific )?information|"
    r"(can'?t|cannot|unable to) (find|provide|confirm|access)|i'?m not sure|not sure", re.IGNORECASE)


def load_questions(path: Path = QA_PATH) -> list[dict[str, Any]]:
    """The evaluation questions."""
    return json.loads(path.read_text(encoding="utf-8"))["questions"]


# ------------------------------------------------------------------ scoring
def _contains(text: str, needle: str) -> bool:
    """Case-insensitive match; numbers must match as whole numbers (so '5' is not found in '25')."""
    pattern = re.escape(needle.lower())
    if needle[:1].isdigit():
        pattern = r"(?<![\d.,])" + pattern + r"(?![\d])"
    elif needle[:1].isalpha():
        pattern = r"(?<![a-z0-9])" + pattern
        pattern += r"(?![a-z0-9])" if needle[-1:].isalpha() else ""
    return re.search(pattern, text.lower()) is not None


def facts_present(text: str, expected: list[Any]) -> bool:
    """True if every expected entry is satisfied (an ``any_of`` entry needs one alternative)."""
    for entry in expected:
        options = entry["any_of"] if isinstance(entry, dict) else [entry]
        if not any(_contains(text, option) for option in options):
            return False
    return True


def says_unknown(text: str) -> bool:
    """True if the baseline explicitly says it does not know / has no information."""
    return bool(DONT_KNOW.search(text))


def score_answer(question: dict[str, Any], answer: Answer, system: str) -> dict[str, Any]:
    """Score one answer; ``system`` is ``A`` (documents) or ``B`` (baseline)."""
    text = answer.text
    if question["answerable"]:
        has_facts = (not answer.refused and facts_present(text, question["expected_answer_contains"])
                     and not any(_contains(text, f) for f in question.get("forbidden_contains", [])))
        cited = (question["source_doc"] in answer.cited_sources) if system == "A" else None
        passed = has_facts and (cited if system == "A" else True)
        return {"passed": passed, "has_facts": has_facts, "cited_correct_document": cited,
                "refused": answer.refused or (system == "B" and says_unknown(text)),
                "hallucination": False}
    refused = answer.refused if system == "A" else says_unknown(text)
    return {"passed": refused, "has_facts": None, "cited_correct_document": None,
            "refused": refused, "hallucination": not refused}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-question scores of one system for one run."""
    answerable = [r for r in rows if r["answerable"]]
    unanswerable = [r for r in rows if not r["answerable"]]
    cited = [r["cited_correct_document"] for r in answerable if r["cited_correct_document"] is not None]
    latencies = [r["latency_seconds"] for r in rows]
    return {
        "questions": len(rows), "passed": sum(r["passed"] for r in rows),
        "accuracy": round(sum(r["passed"] for r in rows) / len(rows), 3),
        "answerable_accuracy": round(sum(r["passed"] for r in answerable) / max(len(answerable), 1), 3),
        "citation_accuracy": round(sum(cited) / len(cited), 3) if cited else None,
        "refusal_accuracy": round(sum(r["refused"] for r in unanswerable) / max(len(unanswerable), 1), 3),
        "hallucinations": sum(r["hallucination"] for r in unanswerable),
        "wrong_or_missed_answerable": sum(not r["passed"] for r in answerable),
        "latency_mean_seconds": round(statistics.mean(latencies), 2),
        "latency_median_seconds": round(statistics.median(latencies), 2),
    }


# ------------------------------------------------------------------ running
def run_once(assistant: Assistant, questions: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Ask every question to both systems once."""
    out: dict[str, list[dict[str, Any]]] = {"A": [], "B": []}
    for q in questions:
        for system in ("A", "B"):
            start = time.perf_counter()
            answer = assistant.ask(q["question"]) if system == "A" else assistant.ask_without_documents(q["question"])
            latency = round(time.perf_counter() - start, 2)
            out[system].append({"id": q["id"], "question": q["question"], "answerable": q["answerable"],
                                "answer": answer.text, "cited_sources": answer.cited_sources,
                                "top_score": answer.top_score, "latency_seconds": latency,
                                **score_answer(q, answer, system)})
            print(f"  [{system}] Q{q['id']:<2} {'PASS' if out[system][-1]['passed'] else 'FAIL'} "
                  f"({latency:.1f}s) {answer.text[:70]!r}")
    return out


def build_config(model: str | None, semantic_only: bool) -> Config:
    """Config copy with the chosen model and retrieval mode."""
    cfg = Config(copy.deepcopy(dict(load_config())))
    if model:
        cfg["llm"]["model"] = model
    if semantic_only:
        cfg["retrieval"]["hybrid"] = False
        cfg["reranker"]["enabled"] = False
    return cfg


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=1, help="repeat everything N times and report variance")
    parser.add_argument("--model", help="Ollama model (default: llm.model in config.yaml)")
    parser.add_argument("--suffix", default="", help="added to the result file name, e.g. __qwen3-8b")
    parser.add_argument("--semantic-only", action="store_true",
                        help="System A without hybrid search and reranker (the Phase 3 behaviour)")
    args = parser.parse_args()
    cfg = build_config(args.model, args.semantic_only)
    llm = OllamaClient(cfg)
    if not llm.is_ready():
        print(f"Ollama is not running or '{cfg.llm.model}' is not pulled. Run `make setup` "
              f"(or `ollama pull {cfg.llm.model}`) and try again. The evaluation needs the real model.",
              file=sys.stderr)
        return 1
    questions = load_questions()
    try:
        assistant = Assistant(cfg, Retriever(cfg), llm)
        runs = []
        for n in range(1, args.runs + 1):
            print(f"Run {n}/{args.runs} with {cfg.llm.model}")
            runs.append(run_once(assistant, questions))
    except EmptyIndexError as exc:
        print(exc, file=sys.stderr)
        return 1

    summaries = {s: [summarize(run[s]) for run in runs] for s in ("A", "B")}
    result = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"), "model": cfg.llm.model, "runs": args.runs,
        "settings": {"temperature": cfg.llm.temperature, "seed": cfg.seed, "hybrid": cfg.retrieval.hybrid,
                     "reranker": cfg.reranker.enabled, "min_retrieval_score": cfg.retrieval.min_retrieval_score,
                     "top_k": cfg.retrieval.top_k},
        "system_a": {"description": "Nexora: retrieval + strict prompt + refusal", "runs": summaries["A"]},
        "system_b": {"description": "Baseline: same model, no documents", "runs": summaries["B"]},
        "accuracy_across_runs": {s: {"mean": round(statistics.mean(x["accuracy"] for x in summaries[s]), 3),
                                     "stdev": round(statistics.pstdev(x["accuracy"] for x in summaries[s]), 3)}
                                 for s in ("A", "B")},
        "questions": [{"id": q["id"], "question": q["question"], "answerable": q["answerable"],
                       "a": [run["A"][i] for run in runs], "b": [run["B"][i] for run in runs]}
                      for i, q in enumerate(questions)],
    }
    name = f"rules_eval_semantic_only{args.suffix}.json" if args.semantic_only else f"rules_eval{args.suffix}.json"
    path = cfg.path("eval_results") / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    for s, label in (("A", "System A (Nexora)"), ("B", "System B (no docs)")):
        first = summaries[s][0]
        print(f"{label}: {first['passed']}/{first['questions']} passed, accuracy {first['accuracy']:.1%}, "
              f"citations {first['citation_accuracy']}, refusals {first['refusal_accuracy']}, "
              f"hallucinations {first['hallucinations']}, latency {first['latency_mean_seconds']}s")
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
