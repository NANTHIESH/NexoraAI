"""Evaluation G: are the document answers faithful to their sources, and does the assistant know when to refuse?

Run: python -m evaluation.eval_faithfulness
Every question (the 15 evaluation questions and the 38 development questions) is answered by the real assistant.
Reported, each with a 95 % interval:
- numeric faithfulness: of the answers that give figures, the share whose figures all appear in the retrieved
  passages (a figure that is in no passage is a hallucination), and whether the cited passage contains them;
- abstention: refusals on answerable questions (should be low) and on unanswerable questions (should be high);
- confidence: does the retrieval score tell right answers from wrong ones? Accuracy per score band, where
  "right" means the assistant answered and cited the document that holds the answer.
The development questions were used to choose the thresholds, so the evaluation questions are reported separately.
Writes evaluation/results/faithfulness_eval.json. Needs Ollama running and the index built (`make index`).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evaluation.eval_rules import load_questions
from evaluation.stats import wilson_interval
from nexora.config import PROJECT_ROOT, load_config
from nexora.llm.ollama_client import OllamaClient
from nexora.rag.generator import Answer, Assistant
from nexora.rag.retriever import Retriever

DEV_PATH = PROJECT_ROOT / "tests" / "fixtures" / "dev_questions.json"
NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
CITATION = re.compile(r"\[[^\[\]]*\]")
SOURCE_MENTION = re.compile(r"\bSOURCES?\s*\d+\b", re.IGNORECASE)  # "According to SOURCE 1" is not a claim
# A weak model sometimes copies the prompt's source header ("SOURCE 1 / Title / Section / Status: ... effective
# 2026-01-01") into its answer. Those lines are prompt scaffolding, not claims, so their numbers are not checked;
# the copying itself is reported separately as "answers that repeat the source block".
SOURCE_HEADER = re.compile(r"^\s*(?:SOURCE\s*\d+|Title:.*|Section:.*|Status:.*)$", re.IGNORECASE | re.MULTILINE)
BANDS = ((0.0, 0.5), (0.5, 0.65), (0.65, 1.01))


def numbers(text: str) -> set[str]:
    """Figures in ``text`` (citations and prompt headers removed), so 1,000 and 1000 are the same."""
    cleaned = SOURCE_MENTION.sub(" ", CITATION.sub(" ", SOURCE_HEADER.sub(" ", text)))
    return {n.replace(",", "") for n in NUMBER.findall(cleaned)}


def _share(k: int, n: int) -> dict[str, Any]:
    low, high = wilson_interval(k, n)
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "ci95": [round(low, 4), round(high, 4)]}


def score(answer: Answer, gold_sources: list[str] | None) -> dict[str, Any]:
    """One answer: refused?, figures supported?, cited passage supports them?, right document cited?"""
    in_sources = set().union(*(numbers(h.chunk.text) for h in answer.sources)) if answer.sources else set()
    claimed = numbers(answer.text) if not answer.refused else set()
    cited = [h for h in answer.sources if h.chunk.source in answer.cited_sources]
    in_cited = set().union(*(numbers(h.chunk.text) for h in cited)) if cited else set()
    return {"refused": answer.refused, "top_score": answer.top_score, "figures": sorted(claimed),
            "echoes_sources": not answer.refused and answer.text.lstrip().upper().startswith("SOURCE "),
            "figures_supported": claimed <= in_sources if claimed else None,
            "cited_supports_figures": (claimed <= in_cited) if claimed and cited else None,
            "right_document": (not answer.refused and bool(set(answer.cited_sources) & set(gold_sources)))
            if gold_sources else None}


def evaluate(model: str | None = None) -> dict[str, Any]:
    """Answer every question and score the answers."""
    cfg = load_config()
    if model:
        cfg = type(cfg)({**cfg, "llm": {**cfg["llm"], "model": model}})
    llm = OllamaClient(cfg)
    if not llm.is_ready():
        raise SystemExit("Ollama is not ready (is it running and is the model pulled?).")
    assistant = Assistant(cfg, Retriever(cfg), llm)
    dev = json.loads(DEV_PATH.read_text(encoding="utf-8"))
    cases: list[dict[str, Any]] = [
        {"set": "evaluation", "question": q["question"], "gold": [q["source_doc"]] if q["answerable"] else None}
        for q in load_questions()]
    cases += [{"set": "development", "question": q["question"], "gold": q["sources"]} for q in dev["answerable"]]
    cases += [{"set": "development", "question": q, "gold": None} for q in dev["unanswerable"]]
    rows = []
    for case in cases:
        answer = assistant.ask(case["question"])
        rows.append({**case, **score(answer, case["gold"]), "answer": answer.text[:200]})
    return summarise(rows, cfg.llm.model)


def summarise(rows: list[dict[str, Any]], model: str) -> dict[str, Any]:
    """Counts with intervals, per question set and over all questions."""
    def group(subset: list[dict[str, Any]]) -> dict[str, Any]:
        answerable = [r for r in subset if r["gold"]]
        unanswerable = [r for r in subset if not r["gold"]]
        answered = [r for r in subset if not r["refused"] and r["figures_supported"] is not None]
        cited = [r for r in answered if r["cited_supports_figures"] is not None]
        given = [r for r in subset if not r["refused"]]
        return {
            "questions": len(subset),
            "answers_that_repeat_the_source_block": _share(sum(bool(r["echoes_sources"]) for r in given), len(given)),
            "refused_when_answerable": _share(sum(r["refused"] for r in answerable), len(answerable)),
            "refused_when_unanswerable": _share(sum(r["refused"] for r in unanswerable), len(unanswerable)),
            "answerable_answered_with_right_document": _share(sum(bool(r["right_document"]) for r in answerable),
                                                              len(answerable)),
            "answers_with_figures_all_supported": _share(sum(bool(r["figures_supported"]) for r in answered),
                                                         len(answered)),
            "cited_passage_contains_the_figures": _share(sum(bool(r["cited_supports_figures"]) for r in cited),
                                                         len(cited)),
        }
    bands = []
    for low, high in BANDS:
        inside = [r for r in rows if r["gold"] and low <= r["top_score"] < high]
        bands.append({"score_from": low, "score_to": min(high, 1.0),
                      "right": _share(sum(bool(r["right_document"]) for r in inside), len(inside))})
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"), "model": model,
        "note": "development questions were used to choose thresholds: read the evaluation set as the independent one",
        "all": group(rows), "evaluation_set": group([r for r in rows if r["set"] == "evaluation"]),
        "development_set": group([r for r in rows if r["set"] == "development"]),
        "accuracy_by_retrieval_score": bands,
        "questions": [{"set": r["set"], "question": r["question"], "answerable": bool(r["gold"]),
                       "refused": r["refused"], "right_document": r["right_document"]} for r in rows],
        "unsupported_figures": [{"question": r["question"], "figures": r["figures"], "answer": r["answer"]}
                                for r in rows if r["figures_supported"] is False],
        "wrong_or_missed": [{"question": r["question"], "refused": r["refused"], "top_score": r["top_score"],
                             "answer": r["answer"]} for r in rows if r["gold"] and not r["right_document"]],
    }


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Faithfulness, refusals and confidence of the document answers")
    parser.add_argument("--model", help="Ollama model to evaluate instead of the configured one")
    parser.add_argument("--suffix", default="", help="added to the result file name, e.g. __qwen3-8b")
    args = parser.parse_args()
    result = evaluate(args.model)
    out: Path = load_config().path("eval_results") / f"faithfulness_eval{args.suffix}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    for name in ("all", "evaluation_set", "development_set"):
        print(f"== {name} ({result[name]['questions']} questions)")
        for key, v in result[name].items():
            if isinstance(v, dict):
                low, high = v["ci95"]
                print(f"   {key:42} {v['k']}/{v['n']} = {v['share']:.0%}  (95% CI {low:.0%} to {high:.0%})"
                      if v["n"] else f"   {key:42} n/a")
    print("== accuracy by retrieval score (answerable questions)")
    for b in result["accuracy_by_retrieval_score"]:
        r = b["right"]
        print(f"   score {b['score_from']:.2f} to {b['score_to']:.2f}: {r['k']}/{r['n']} right")
    for u in result["unsupported_figures"]:
        print(f"UNSUPPORTED FIGURES {u['figures']} in: {u['answer']!r}")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
