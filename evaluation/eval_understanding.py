"""Evaluation D: does the language-understanding step beat the keyword router?

Run: python -m evaluation.eval_understanding [--model qwen2.5:7b]
For each labelled message (evaluation/understanding_set.json) the keyword router (with the rule-based
follow-up rewrite) and the model-based understanding each choose an intent; "clarify" counts as an intent.
Reported: accuracy with a 95 % interval (overall and per tag), a paired test of model vs keywords, how often the
model asks a clarifying question when the message was clear, whether the rewritten question keeps names and
numbers, and latency. "Deployed" applies the guard used in the chat: a clarifying question is dropped when the
keyword rules already recognise a data question.
Writes evaluation/results/understanding_eval.json. Needs Ollama running.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from typing import Any

from evaluation.stats import mcnemar_exact, paired_counts, percentile, wilson_interval
from nexora.agents.lookups import lookup_kind
from nexora.agents.memory import Turn, rewrite_question
from nexora.agents.router import keyword_route
from nexora.agents.spending import is_spending_question
from nexora.agents.understanding import CLARIFY, understand
from nexora.config import PROJECT_ROOT, load_config
from nexora.db.repositories import MissingDataError, frames_from_db
from nexora.db.session import connect
from nexora.llm.ollama_client import OllamaClient

SET_PATH = PROJECT_ROOT / "evaluation" / "understanding_set.json"
WHO = "Ruth, CEO & Owner (role: owner)"


def _share(k: int, n: int) -> dict[str, Any]:
    low, high = wilson_interval(k, n)
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "ci95": [round(low, 4), round(high, 4)]}


def _by_tag(items: list[dict[str, Any]], correct: dict[int, bool]) -> dict[str, Any]:
    tags = sorted({it["tag"] for it in items})
    return {t: _share(sum(correct[i] for i, it in enumerate(items) if it["tag"] == t),
                      sum(1 for it in items if it["tag"] == t)) for t in tags}


def _deployed_intent(text: str, predicted: str, effective: str, keyword: str, frames) -> str:  # noqa: ANN001
    """The intent the chat finally uses: the model's choice, then the same overrides as ``Orchestrator.chat``."""
    if predicted == CLARIFY and keyword_route(text) != "knowledge":
        return keyword  # a clarifying question is dropped when the rules know the topic
    if predicted in (CLARIFY, "smalltalk"):
        return predicted
    if lookup_kind(effective, frames) is None and lookup_kind(text, frames) is not None:
        effective = text  # the exact rules win over the model's wording
    kind = lookup_kind(effective, frames)
    if kind in ("tool_facts", "inventory"):
        return "subscription"
    return "report" if (kind or is_spending_question(effective)) else predicted


def evaluate(model: str | None = None) -> dict[str, Any]:
    """Run both understanders over the labelled set and score them."""
    cfg = load_config()
    if model:
        cfg = type(cfg)({**cfg, "llm": {**cfg["llm"], "model": model}})
    llm = OllamaClient(cfg)
    if not llm.is_ready():
        raise SystemExit("Ollama is not ready (is it running and is the model pulled?).")
    items = json.loads(SET_PATH.read_text(encoding="utf-8"))["items"]
    try:
        frames = frames_from_db(connect(cfg.path("sqlite")))
    except MissingDataError:
        frames = None  # the tool-name lookups then are not applied
    keyword_ok, model_ok, deployed_ok = {}, {}, {}
    rows, latencies = [], []
    fallbacks = clear_clarified = clear_total = kept = kept_total = 0
    for i, it in enumerate(items):
        history = [Turn(q, a) for q, a in it.get("history", [])]
        keyword = keyword_route(rewrite_question(it["text"], history, None))
        start = time.perf_counter()
        understood = understand(it["text"], history, WHO, llm)
        latencies.append(time.perf_counter() - start)
        if understood is None:
            fallbacks += 1
            predicted = keyword  # as deployed: the keyword router takes over
        else:
            predicted = understood.intent
        effective = understood.question if understood else rewrite_question(it["text"], history, None)
        deployed = _deployed_intent(it["text"], predicted, effective, keyword, frames)
        keyword_ok[i], model_ok[i], deployed_ok[i] = keyword == it["intent"], predicted == it["intent"], \
            deployed == it["intent"]
        if it["intent"] != CLARIFY:
            clear_total += 1
            clear_clarified += predicted == CLARIFY
        if it.get("must_keep") and understood is not None:
            kept_total += 1
            kept += all(w.lower() in understood.question.lower() for w in it["must_keep"])
        rows.append({"text": it["text"], "tag": it["tag"], "expected": it["intent"], "keyword": keyword,
                     "model": predicted, "rewritten": understood.question if understood else None,
                     "model_correct": model_ok[i], "deployed_correct": deployed_ok[i]})
    only_model, only_keyword = paired_counts(model_ok, keyword_ok)
    vague = [i for i, it in enumerate(items) if it["intent"] == CLARIFY]
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"), "model": cfg.llm.model, "n": len(items),
        "note": "labels are a judgement call by the team; the messages are not in the prompt's few-shot examples",
        "intent_accuracy": {"keywords": _share(sum(keyword_ok.values()), len(items)),
                            "model": _share(sum(model_ok.values()), len(items)),
                            "model_as_deployed": _share(sum(deployed_ok.values()), len(items))},
        "deployed_note": "model_as_deployed = the model's intent plus the same rule overrides the chat applies",
        "by_tag": {"keywords": _by_tag(items, keyword_ok), "model": _by_tag(items, model_ok)},
        "paired_model_vs_keywords": {"only_model_right": only_model, "only_keywords_right": only_keyword,
                                     "p_value": round(mcnemar_exact(only_model, only_keyword), 4)},
        "unneeded_clarifications": _share(clear_clarified, clear_total),
        "vague_messages_clarified": _share(sum(model_ok[i] for i in vague), len(vague)),
        "rewrite_keeps_names_and_numbers": _share(kept, kept_total),
        "model_unusable_answers": _share(fallbacks, len(items)),
        "latency_seconds": {"p50": round(percentile(latencies, 50), 2), "p95": round(percentile(latencies, 95), 2)},
        "misses": [r for r in rows if r["model"] != r["expected"] or r["keyword"] != r["expected"]],
        "items": rows,  # every message with its result, for paired comparisons between models
    }


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--model", help="Ollama model to evaluate instead of the configured one")
    parser.add_argument("--suffix", default="", help="added to the result file name, e.g. __qwen3-8b")
    args = parser.parse_args()
    result = evaluate(args.model)
    out = load_config().path("eval_results") / f"understanding_eval{args.suffix}.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    acc = result["intent_accuracy"]
    for name in ("keywords", "model", "model_as_deployed"):
        a = acc[name]
        print(f"{name:18} {a['k']}/{a['n']} = {a['share']:.1%}  95% CI {a['ci95'][0]:.0%} to {a['ci95'][1]:.0%}")
    p = result["paired_model_vs_keywords"]
    print(f"paired: only model right {p['only_model_right']}, only keywords right {p['only_keywords_right']}, "
          f"p = {p['p_value']}")
    unneeded = result["unneeded_clarifications"]
    print(f"unneeded clarifications: {unneeded['k']}/{unneeded['n']}; "
          f"vague clarified: {result['vague_messages_clarified']['k']}/{result['vague_messages_clarified']['n']}")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
