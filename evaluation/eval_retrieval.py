"""Retrieval-only comparison of search modes on the DEVELOPMENT questions (not the 15 eval questions).

Run: python -m evaluation.eval_retrieval
Compares semantic, hybrid (BM25 + embeddings), semantic + reranker and hybrid + reranker:
hit@1/3/5 and MRR for answerable questions, plus how well the top embedding score separates
answerable from unanswerable questions at the configured refusal threshold.
Writes evaluation/results/retrieval_dev_eval.json. No LLM is involved.
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from nexora.config import PROJECT_ROOT, Config, load_config
from nexora.rag.retriever import Retriever
from nexora.rag.vectorstore import EmptyIndexError

DEV_QUESTIONS = PROJECT_ROOT / "tests" / "fixtures" / "dev_questions.json"
MODES = {"semantic": (False, False), "hybrid": (True, False),
         "semantic+rerank": (False, True), "hybrid+rerank": (True, True)}


def mode_config(base: Config, hybrid: bool, rerank: bool) -> Config:
    """Copy of ``base`` with the retrieval mode switched."""
    cfg = Config(copy.deepcopy(dict(base)))
    cfg["retrieval"]["hybrid"] = hybrid
    cfg["reranker"]["enabled"] = rerank
    return cfg


def evaluate_mode(cfg: Config, dev: dict) -> dict:
    """Metrics for one retrieval mode."""
    retriever = Retriever(cfg)
    ranks, misses = [], []
    top_answerable, top_unanswerable = [], []
    for item in dev["answerable"]:
        res = retriever.retrieve(item["question"])
        sources = [h.chunk.source for h in res.hits]
        rank = next((i + 1 for i, s in enumerate(sources) if s in item["sources"]), None)
        ranks.append(rank)
        if rank != 1:
            misses.append({"question": item["question"], "rank": rank, "got": sources[:3]})
        top_answerable.append(res.top_score)
    for question in dev["unanswerable"]:
        top_unanswerable.append(retriever.retrieve(question).top_score)
    n = len(ranks)
    threshold = cfg.retrieval.min_retrieval_score
    return {
        "hit@1": round(sum(r == 1 for r in ranks) / n, 3),
        "hit@3": round(sum(r is not None and r <= 3 for r in ranks) / n, 3),
        "hit@5": round(sum(r is not None for r in ranks) / n, 3),
        "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
        "answerable_passing_threshold": round(sum(s >= threshold for s in top_answerable) / n, 3),
        "unanswerable_refused_by_threshold": round(
            sum(s < threshold for s in top_unanswerable) / len(top_unanswerable), 3),
        "min_answerable_top_score": round(min(top_answerable), 3),
        "max_unanswerable_top_score": round(max(top_unanswerable), 3),
        "not_ranked_first": misses,
    }


def main() -> int:
    """Run all modes and save the comparison."""
    base = load_config()
    dev = json.loads(DEV_QUESTIONS.read_text(encoding="utf-8"))
    try:
        results = {name: evaluate_mode(mode_config(base, *flags), dev) for name, flags in MODES.items()}
    except EmptyIndexError as exc:
        print(exc, file=sys.stderr)
        return 1
    out = {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": "Development questions only (tests/fixtures/dev_questions.json); not the evaluation set.",
        "n_answerable": len(dev["answerable"]), "n_unanswerable": len(dev["unanswerable"]),
        "min_retrieval_score": base.retrieval.min_retrieval_score, "modes": results,
    }
    path = Path(base.path("eval_results")) / "retrieval_dev_eval.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"{'mode':<18}{'hit@1':>7}{'hit@3':>7}{'hit@5':>7}{'MRR':>7}{'pass':>7}{'refuse':>8}")
    for name, m in results.items():
        print(f"{name:<18}{m['hit@1']:>7}{m['hit@3']:>7}{m['hit@5']:>7}{m['mrr']:>7}"
              f"{m['answerable_passing_threshold']:>7}{m['unanswerable_refused_by_threshold']:>8}")
    print(f"Saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
