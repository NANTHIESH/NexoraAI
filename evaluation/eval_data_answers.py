"""Evaluation E: are the computed data answers exactly right?

Run: python -m evaluation.eval_data_answers
Method: exact match against an independent ground truth. For three random seeds the synthetic company data is
generated and loaded; questions are generated from templates (income, spending per tool and per category, task
counts, headcount, subscription cost, seats and count, biggest expense). The true answer of every question is
computed here with plain pandas on the raw CSV files, sharing no code with the assistant's lookups, and the
assistant's answer must contain every expected number. The language model is switched off, so this tests the
routing rules and the calculations that produce amounts, counts and facts (they are never written by the model).
Reported: accuracy with a 95 % interval overall and per question type, plus every failure.
Writes evaluation/results/data_answers_eval.json. Needs no Ollama.
"""

from __future__ import annotations

import calendar
import copy
import json
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
from scripts.generate_data import generate
from scripts.load_db import load_db

from evaluation.stats import wilson_interval
from nexora.agents.orchestrator import Orchestrator
from nexora.config import Config, load_config
from nexora.db.session import connect
from nexora.llm.ollama_client import OllamaUnavailableError
from nexora.security.auth import get_user

SEEDS = (42, 7, 123)
MONTHS_PER_SEED = 6        # every other month of the 12
ITEMS_PER_MONTH = 2        # tools / categories asked about per month
TOOLS_ASKED = 8


class _NoModel:
    """Ollama stand-in that is never ready: only the rule-based paths run."""

    model = "none"

    def is_ready(self) -> bool:
        return False

    def chat(self, *_: object, **__: object) -> str:
        raise OllamaUnavailableError("model switched off for this evaluation")


class _NoDocuments:
    def ask(self, *_: object, **__: object) -> None:
        raise RuntimeError("documents are not part of this evaluation")


def _money(value: float) -> str:
    return f"{abs(value):,.2f}"


def _month_name(month: str) -> str:
    year, number = month.split("-")
    return f"{calendar.month_name[int(number)]} {year}"


def build_cases(folder: Path) -> list[dict[str, Any]]:
    """Questions with their expected strings, from the raw CSV files (independent of the assistant's code)."""
    tx = pd.read_csv(folder / "transactions.csv", keep_default_na=False)
    tx["month"] = tx["date"].str[:7]
    tasks = pd.read_csv(folder / "tasks.csv", keep_default_na=False)
    staff = pd.read_csv(folder / "employees.csv", keep_default_na=False)
    subs = pd.read_csv(folder / "subscriptions.csv", keep_default_na=False)
    usage = pd.read_csv(folder / "usage_log.csv", keep_default_na=False)
    months = sorted(tx["month"].unique())[::2][:MONTHS_PER_SEED]
    cases: list[dict[str, Any]] = []

    def add(kind: str, question: str, expected: list[str], intent: str = "report") -> None:
        cases.append({"type": kind, "question": question, "expected": expected, "intent": intent})

    for m in months:
        rows = tx[tx["month"] == m]
        income, expenses = rows.loc[rows["amount"] > 0, "amount"].sum(), -rows.loc[rows["amount"] < 0, "amount"].sum()
        add("income and expenses", f"What was our income in {_month_name(m)}?",
            [_money(income), _money(expenses), _money(income - expenses)])
        spend = rows[rows["amount"] < 0]
        for tool in sorted(set(subs["tool"]) & set(spend["counterparty"]))[:ITEMS_PER_MONTH]:
            total = -spend.loc[spend["counterparty"] == tool, "amount"].sum()
            add("spending per tool", f"How much did we spend on {tool} in {_month_name(m)}?", [_money(total)])
        cats = sorted(c for c in spend["category"].unique() if c not in ("Software", "Cloud"))[:ITEMS_PER_MONTH]
        for cat in cats:
            total = -spend.loc[spend["category"] == cat, "amount"].sum()
            add("spending per category", f"How much did we spend on {cat.lower()} in {_month_name(m)}?",
                [_money(total)])
    for status, words in (("blocked", "blocked"), ("done", "done"), ("in_progress", "in progress")):
        add("task counts", f"How many tasks are {words}?", [f"{int((tasks['status'] == status).sum())} tasks"])
    add("headcount", "How many employees do we have?", [f"{len(staff)} employees"])
    last = usage[usage["month"] == usage["month"].max()].set_index("tool")
    for row in subs.head(TOOLS_ASKED).itertuples():
        add("subscription price", f"What does {row.tool} cost?", [_money(row.monthly_cost)], "subscription")
        if row.tool in last.index:
            add("subscription seats", f"How many people use {row.tool}?",
                [f"{int(last.loc[row.tool, 'active_users'])} of {int(row.seats)} seats"], "subscription")
    total = subs["monthly_cost"].sum()
    for question in ("How many subscriptions do we have?", "how many tools do we pay for"):
        add("subscription count", question, [f"{len(subs)} software subscriptions", _money(total)], "subscription")
    by_cat = -tx[tx["amount"] < 0].groupby("category")["amount"].sum()
    add("biggest expense", "What is our biggest expense?", [by_cat.idxmax(), _money(by_cat.max())])
    return cases


def run_seed(seed: int) -> list[dict[str, Any]]:
    """Generate the data for ``seed``, ask every question and compare with the ground truth."""
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        generate(seed, base / "gen")
        cfg = Config(copy.deepcopy(dict(load_config())))
        cfg["paths"].update(generated=str(base / "gen"), sqlite=str(base / "eval.sqlite"))
        cfg["auth"]["bcrypt_rounds"] = 4
        load_db(cfg)
        conn = connect(base / "eval.sqlite")
        orchestrator = Orchestrator(cfg, _NoDocuments(), _NoModel())
        owner = get_user(conn, "E001")
        results = []
        for n, case in enumerate(build_cases(base / "gen")):
            answer = orchestrator.chat(conn, owner, case["question"], f"eval-{seed}-{n}")
            text = answer.text.lower()
            missing = [e for e in case["expected"] if e.lower() not in text]
            results.append({**case, "seed": seed, "intent_ok": answer.intent == case["intent"],
                            "passed": not missing and answer.intent == case["intent"],
                            "missing": missing, "got": answer.text[:240], "text": text})
        conn.close()
        return results


def _share(k: int, n: int) -> dict[str, Any]:
    low, high = wilson_interval(k, n)
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "ci95": [round(low, 4), round(high, 4)]}


def evaluate() -> dict[str, Any]:
    """All seeds, scored."""
    results = [r for seed in SEEDS for r in run_seed(seed)]
    types = sorted({r["type"] for r in results})
    # Negative control: an answer must NOT satisfy the expectations of a different question. If it often did,
    # the check would be too lenient to mean anything.
    shifted = sum(all(e.lower() in r["text"] for e in results[(i + 7) % len(results)]["expected"])
                  for i, r in enumerate(results))
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"), "seeds": list(SEEDS),
        "note": "ground truth computed with plain pandas on the raw CSV files; the language model is switched off",
        "overall": _share(sum(r["passed"] for r in results), len(results)),
        "negative_control": {**_share(shifted, len(results)),
                             "meaning": "answers that also pass with another question's numbers (should be ~0)"},
        "by_type": {t: _share(sum(r["passed"] for r in results if r["type"] == t),
                              sum(1 for r in results if r["type"] == t)) for t in types},
        "failures": [{k: r[k] for k in ("seed", "type", "question", "expected", "missing", "intent", "got")}
                     for r in results if not r["passed"]],
    }


def main() -> int:
    """CLI entry point."""
    result = evaluate()
    out = load_config().path("eval_results") / "data_answers_eval.json"
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    o = result["overall"]
    print(f"overall {o['k']}/{o['n']} = {o['share']:.1%}  95% CI {o['ci95'][0]:.1%} to {o['ci95'][1]:.1%}")
    nc = result["negative_control"]
    print(f"negative control: {nc['k']}/{nc['n']} answers wrongly pass with another question's expectations")
    for name, s in result["by_type"].items():
        print(f"  {name:22} {s['k']}/{s['n']}")
    for f in result["failures"][:12]:
        print(f"FAIL seed {f['seed']} [{f['type']}] {f['question']!r} missing {f['missing']}\n   got: {f['got']}")
    print(f"written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
