"""Report agent: Python computes every number, the LLM only words the summary.

The summary is checked: if the model writes a number that is not in the data it was given,
the deterministic template summary is used instead.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Protocol

import pandas as pd

from nexora.agents.tools import PERIODS, format_eur
from nexora.db.repositories import Frames

BLOCKED_LIST_LIMIT = 10
NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")
SUMMARY_PROMPT = ("You write short business summaries for an office manager. Use ONLY the facts and numbers "
                  "in the data below. Do not calculate, estimate or add any number that is not in the data. "
                  "Write 3 or 4 plain sentences, no bullet points, no markdown.")


class SupportsChat(Protocol):
    """Anything with the OllamaClient.chat signature."""

    def chat(self, system: str, user: str, json_mode: bool = False) -> str: ...


@dataclass(frozen=True)
class Report:
    """Computed figures, a text summary and who wrote the summary (``llm`` or ``template``)."""

    data: dict[str, Any]
    summary: str
    summary_source: str


def window(period: str, ref: date) -> tuple[date, date, date, date]:
    """(start, end, previous_start, previous_end) for a period ending at ``ref``."""
    if period == "weekly":
        start, end = ref - timedelta(days=6), ref
        return start, end, start - timedelta(days=7), start - timedelta(days=1)
    ref_month = pd.Period(ref, "M")
    if period == "monthly":
        first, last = ref_month, ref_month
        before_first = before_last = ref_month - 1
    else:
        first, last = ref_month - 11, ref_month
        before_first, before_last = ref_month - 23, ref_month - 12
    return (first.start_time.date(), last.end_time.date(),
            before_first.start_time.date(), before_last.end_time.date())


def _spend_by_category(tx: pd.DataFrame, start: date, end: date) -> dict[str, float]:
    mask = (tx["date"].dt.date >= start) & (tx["date"].dt.date <= end) & (tx["amount"] < 0)
    return {k: round(float(v), 2) for k, v in tx[mask].groupby("category")["amount"].sum().abs().items()}


def compute_report(frames: Frames, period: str, ref_date: date | None = None) -> dict[str, Any]:
    """All report numbers for ``period`` (weekly | monthly | yearly), computed with pandas."""
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}, got {period!r}")
    tx = frames.transactions
    ref = ref_date or tx["date"].max().date()
    start, end, prev_start, prev_end = window(period, ref)
    in_window = tx[(tx["date"].dt.date >= start) & (tx["date"].dt.date <= end)]
    income = round(float(in_window.loc[in_window["amount"] > 0, "amount"].sum()), 2)
    by_category = _spend_by_category(tx, start, end)
    expenses = round(sum(by_category.values()), 2)

    previous = _spend_by_category(tx, prev_start, prev_end)
    increases = []
    if previous:
        for category in by_category:
            delta = round(by_category[category] - previous.get(category, 0.0), 2)
            if delta > 0:
                increases.append({"category": category, "current": by_category[category],
                                  "previous": previous.get(category, 0.0), "increase": delta})
    increases = sorted(increases, key=lambda x: -x["increase"])[:3]

    tasks = frames.tasks
    ts_end = pd.Timestamp(end)
    existing = tasks[tasks["created"] <= ts_end]
    done = tasks[(tasks["completed"] >= pd.Timestamp(start)) & (tasks["completed"] <= ts_end)]
    blocked = existing[existing["status"] == "blocked"]
    overdue = existing[(existing["status"] != "done") & (existing["due"] < ts_end)]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    blocked_list = [{"task_id": r.task_id, "project": r.project, "title": r.title, "owner": r.owner,
                     "priority": r.priority, "due": r.due.date().isoformat()}
                    for r in sorted(blocked.itertuples(), key=lambda r: (order[r.priority], r.due))
                    ][:BLOCKED_LIST_LIMIT]
    return {
        "period": period, "start": start.isoformat(), "end": end.isoformat(),
        "income": income, "expenses": expenses, "net": round(income - expenses, 2),
        "expenses_by_category": dict(sorted(by_category.items(), key=lambda kv: -kv[1])),
        "top_cost_increases": increases,
        "tasks": {"done": len(done), "blocked": len(blocked), "overdue": len(overdue)},
        "blocked_tasks": blocked_list,
    }


def _facts(data: dict[str, Any]) -> str:
    """Plain-text facts handed to the LLM (all numbers already formatted)."""
    top = ", ".join(f"{c} {format_eur(v)}" for c, v in list(data["expenses_by_category"].items())[:3])
    rises = "; ".join(f"{i['category']} up {format_eur(i['increase'])}" for i in data["top_cost_increases"])
    t = data["tasks"]
    return (f"Period: {data['period']} report from {data['start']} to {data['end']}\n"
            f"Income: {format_eur(data['income'])}\nExpenses: {format_eur(data['expenses'])}\n"
            f"Net: {format_eur(data['net'])}\nLargest expense categories: {top or 'none'}\n"
            f"Biggest cost increases versus the previous period: {rises or 'none'}\n"
            f"Tasks: {t['done']} done, {t['blocked']} blocked, {t['overdue']} overdue")


def template_summary(data: dict[str, Any]) -> str:
    """Deterministic summary used when the model is unavailable or writes unsupported numbers."""
    t = data["tasks"]
    rises = ", ".join(f"{i['category']} (+{format_eur(i['increase'])})" for i in data["top_cost_increases"])
    text = (f"{data['period'].capitalize()} report {data['start']} to {data['end']}: income "
            f"{format_eur(data['income'])}, expenses {format_eur(data['expenses'])}, net "
            f"{format_eur(data['net'])}. ")
    text += f"Biggest cost increases: {rises}. " if rises else "No cost increases versus the previous period. "
    return text + f"Tasks: {t['done']} done, {t['blocked']} blocked, {t['overdue']} overdue."


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in NUMBER.findall(text)}


def build_report(frames: Frames, period: str, ref_date: date | None = None,
                 llm: SupportsChat | None = None) -> Report:
    """Compute the report and add a summary (LLM-written from the facts, or the template)."""
    data = compute_report(frames, period, ref_date)
    facts = _facts(data)
    if llm is not None:
        try:
            text = llm.chat(SUMMARY_PROMPT, facts).strip()
        except Exception:  # noqa: BLE001 - Ollama missing, model not pulled, timeouts...
            text = ""
        if text and _numbers(text) <= _numbers(facts):
            return Report(data, text, "llm")
    return Report(data, template_summary(data), "template")
