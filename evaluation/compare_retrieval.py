"""Evaluation J: which retrieval setup finds the right passage best?

Run: python -m evaluation.compare_retrieval
Compares embedding models (the default MiniLM and two models served by Ollama), with and without the
cross-encoder reranker, with and without query expansion (rewordings written by the local Ollama model):

- DEVELOPMENT questions (tests/fixtures/dev_questions.json, right document known): used to choose settings.
- HELD-OUT questions (evaluation/retrieval_test_set.json, right passage known): written before any change was
  tried and never used to choose anything. These are the numbers to trust.

Per setup and question set: hit@1/3/5 and MRR (passage level on the held-out set), plus how well the best
similarity score separates answerable from unanswerable questions: AUC (1.0 = perfect) and the accuracy at a
refusal threshold chosen on the development questions only. Everything runs in a temporary directory, so the
real index is not touched. Writes evaluation/results/retrieval_comparison.json. Needs Ollama (embedding models
and, for expansion, the chat model).
"""

from __future__ import annotations

import copy
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from scripts.build_index import build_index

from evaluation.stats import wilson_interval
from nexora.config import PROJECT_ROOT, Config, load_config
from nexora.llm.ollama_client import OllamaClient
from nexora.rag.retriever import Retriever
from nexora.rag.vectorstore import VectorStore, collection_name

DEV = PROJECT_ROOT / "tests" / "fixtures" / "dev_questions.json"
TEST = PROJECT_ROOT / "evaluation" / "retrieval_test_set.json"
MINILM = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDINGS = {"MiniLM (current)": MINILM, "nomic-embed-text": "ollama:nomic-embed-text", "bge-m3": "ollama:bge-m3"}
EXPANSION = 2


def setup(cfg: Config, embedding: str, rerank: bool, expansion: int, tmp: Path) -> Config:
    """Copy of ``cfg`` with the chosen embedding model, reranker and expansion, pointing into ``tmp``."""
    out = Config(copy.deepcopy(dict(cfg)))
    out["embeddings"]["model"] = embedding
    out["reranker"]["enabled"] = rerank
    out["retrieval"]["hybrid"] = True
    out["retrieval"]["query_expansion"] = expansion
    out["paths"]["chroma"] = str(tmp / "chroma")
    out["paths"]["sqlite"] = str(tmp / "eval.sqlite")
    return out


def rank_of(hits: list[Any], gold: list[tuple[str, str]] | None, docs: list[str] | None) -> int | None:
    """1-based position of the first hit that is a gold passage (``gold``) or a gold document (``docs``)."""
    for i, hit in enumerate(hits, start=1):
        if gold is not None and (hit.chunk.source, hit.chunk.section) in gold:
            return i
        if docs is not None and hit.chunk.source in docs:
            return i
    return None


def summarise_ranks(ranks: list[int | None]) -> dict[str, Any]:
    """hit@1/3/5 with a 95 % interval for hit@1, and MRR."""
    n = len(ranks)
    k1 = sum(r == 1 for r in ranks)
    low, high = wilson_interval(k1, n)
    return {"n": n, "hit@1": round(k1 / n, 3), "hit@1_ci95": [round(low, 3), round(high, 3)],
            "hit@3": round(sum(r is not None and r <= 3 for r in ranks) / n, 3),
            "hit@5": round(sum(r is not None for r in ranks) / n, 3),
            "mrr": round(sum(1 / r for r in ranks if r) / n, 3)}


def auc(answerable: list[float], unanswerable: list[float]) -> float:
    """Share of (answerable, unanswerable) pairs where the answerable question scores higher (ties count half)."""
    pairs = [(a, u) for a in answerable for u in unanswerable]
    return round(sum(1.0 if a > u else 0.5 if a == u else 0.0 for a, u in pairs) / len(pairs), 3)


def best_threshold(answerable: list[float], unanswerable: list[float]) -> float:
    """Refusal threshold with the best balanced accuracy on the given scores (chosen on development only)."""
    scores = sorted(set(answerable + unanswerable))
    candidates = [(a + b) / 2 for a, b in zip(scores, scores[1:], strict=False)] or [scores[0]]
    def balanced(t: float) -> float:
        return (sum(s >= t for s in answerable) / len(answerable) + sum(s < t for s in unanswerable) / len(unanswerable)) / 2
    return round(max(candidates, key=balanced), 3)


def evaluate(retriever: Retriever, dev: dict, test: dict) -> dict[str, Any]:
    """All metrics of one retrieval setup."""
    started = time.perf_counter()
    dev_found = [retriever.retrieve(i["question"]) for i in dev["answerable"]]
    test_found = [retriever.retrieve(i["question"]) for i in test["answerable"]]
    dev_hits, test_hits = [f.hits for f in dev_found], [f.hits for f in test_found]
    dev_top, test_top = [f.top_score for f in dev_found], [f.top_score for f in test_found]
    dev_un = [retriever.retrieve(q).top_score for q in dev["unanswerable"]]
    test_un = [retriever.retrieve(q).top_score for q in test["unanswerable"]]
    seconds = (time.perf_counter() - started) / (len(dev_found) + len(test_found) + len(dev_un) + len(test_un))
    gold = [[tuple(g) for g in item["gold"]] for item in test["answerable"]]
    threshold = best_threshold(dev_top, dev_un)
    return {
        "development_document": summarise_ranks([rank_of(h, None, item["sources"]) for h, item in zip(dev_hits, dev["answerable"], strict=True)]),
        "heldout_passage": summarise_ranks([rank_of(h, g, None) for h, g in zip(test_hits, gold, strict=True)]),
        "heldout_document": summarise_ranks([rank_of(h, None, sorted({s for s, _ in g}))
                                             for h, g in zip(test_hits, gold, strict=True)]),
        "refusal": {"auc_development": auc(dev_top, dev_un), "auc_heldout": auc(test_top, test_un),
                    "threshold_chosen_on_development": threshold,
                    "heldout_answerable_kept": round(sum(s >= threshold for s in test_top) / len(test_top), 3),
                    "heldout_unanswerable_refused": round(sum(s < threshold for s in test_un) / len(test_un), 3)},
        "seconds_per_query": round(seconds, 3),
    }


def main() -> int:
    """Run every setup and save the comparison."""
    base = load_config()
    dev = json.loads(DEV.read_text(encoding="utf-8"))
    test = json.loads(TEST.read_text(encoding="utf-8"))
    llm = OllamaClient(base)
    if not llm.is_ready():
        print("Ollama is not ready.", file=sys.stderr)
        return 1
    questions = [i["question"] for i in dev["answerable"] + test["answerable"]] + dev["unanswerable"] + test["unanswerable"]
    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as raw:
        tmp = Path(raw)
        expansions: dict[str, list[str]] = {}
        expander = Retriever(setup(base, MINILM, False, EXPANSION, tmp), store=_empty_store(tmp), llm=llm)
        print(f"rewording {len(questions)} questions with {base.llm.model} ...", flush=True)
        for n, q in enumerate(questions, start=1):
            expander._expand(q)
            if n % 20 == 0:
                print(f"  {n}/{len(questions)}", flush=True)
        expansions = expander._expansions
        for emb_name, emb in EMBEDDINGS.items():
            cfg0 = setup(base, emb, False, 0, tmp)
            store = VectorStore(Path(cfg0.path("chroma")), collection_name(cfg0))
            print(f"indexing with {emb} ...", flush=True)
            build_index(cfg0, store=store)
            for rerank in (True, False):
                for expansion in (0, EXPANSION):
                    cfg = setup(base, emb, rerank, expansion, tmp)
                    retriever = Retriever(cfg, store=store)
                    retriever._expansions = expansions
                    name = f"{emb_name} | {'rerank' if rerank else 'no rerank'} | {'expansion' if expansion else 'no expansion'}"
                    results[name] = evaluate(retriever, dev, test)
                    r = results[name]
                    print(f"{name:55} dev {r['development_document']['hit@1']:.2f} mrr {r['development_document']['mrr']:.2f} | "
                          f"held-out passage hit@1 {r['heldout_passage']['hit@1']:.2f} hit@3 {r['heldout_passage']['hit@3']:.2f} "
                          f"mrr {r['heldout_passage']['mrr']:.2f} | auc {r['refusal']['auc_heldout']:.2f}", flush=True)
    out = {"run_at": datetime.now(UTC).isoformat(timespec="seconds"), "expansion_queries": EXPANSION,
           "expansion_model": base.llm.model, "setups": results,
           "example_expansions": {q: expansions[q] for q in list(expansions)[:6]}}
    path = Path(base.path("eval_results")) / "retrieval_comparison.json"
    path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"written to {path}")
    return 0


def _empty_store(tmp: Path) -> VectorStore:
    """A throw-away store so that the expansion helper never touches the real index."""
    return VectorStore(tmp / "unused-chroma", "unused")


if __name__ == "__main__":
    sys.exit(main())
