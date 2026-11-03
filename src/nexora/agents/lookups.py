"""Computed answers for common data questions (income, tasks, rankings, headcount, tool facts).

Every number comes from pandas over the business tables; the language model is not involved.
Together with spending.py this replaces "answer a data question with the generic report".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from nexora.agents.spending import eur, is_spending_question, parse_months
from nexora.config import Config
from nexora.db.repositories import Frames
from nexora.detectors import Flag

MAX_LISTED = 10
INCOME_WORDS = re.compile(r"\b(income|revenue|revenues|profit|profits|turnover|earn|earned|earnings|net result)\b", re.I)
REPORT_WORDS = re.compile(r"\b(report|summary|overview|numbers|results|performance|how (are|did) we)\b", re.I)
TASK_WORDS = re.compile(r"\b(tasks?|issues?|tickets?|bugs?|stories|sprint)\b", re.I)
RANKING_WORDS = re.compile(r"\b(biggest|largest|highest|top|greatest|most expensive|costliest)\b", re.I)
MONEY_WORDS = re.compile(r"\b(expenses?|costs?|spend|spending|spent|categor\w+|vendors?|suppliers?)\b", re.I)
HEADCOUNT = re.compile(r"\bhow many (employees|people|staff|developers|engineers|team members|colleagues)\b", re.I)
FACT_WORDS = re.compile(r"\b(cost|costs|price|much|seats?|users?|use|uses|using|plan|owner|owns|billing|billed|"
                        r"subscription|licen[cs]es?)\b", re.I)
SAVINGS_WORDS = re.compile(r"\b(cancel|save|saving|savings|waste|wasteful|unused|duplicate|twice|overlap|issues?|"
                           r"problems?|recommend\w*)\b", re.I)
STATUS_WORDS = {"blocked": r"\bblocked\b", "overdue": r"\b(overdue|late|past due)\b",
                "done": r"\b(done|completed|finished|closed)\b", "in_progress": r"\bin progress\b",
                "todo": r"\b(todo|to do|open|pending|backlog|not started)\b"}
TOPICS = {"unused_subscription": r"\b(unused|not used|no users|nobody uses|idle)\b",
          "duplicate_charge": r"\b(duplicates?|twice|double[- ]?charg\w*)\b",
          "price_increase": r"\b(price (increase|rise|hike)s?|more expensive|increased|raised)\b",
          "overlap": r"\b(overlap\w*|redundant|same job|two tools)\b",
          "unlisted_charge": r"\b(unlisted|not on the (subscription )?list|unknown (tools?|charges?))\b"}
INVENTORY = re.compile(
    r"\bhow many (software |saas |paid )?(subscriptions?|tools|licen[cs]es|apps|products)\b"
    r"|\b(list|show|name|what|which)\b.{0,25}\b(subscriptions?|tools|software|apps)\b.{0,25}"
    r"\b(have|use|using|pay|paying|subscribed|own)\b"
    r"|\b(total|all)\b.{0,15}\b(subscriptions?|software tools)\b"
    r"|\b(list|show|name)\b( me)?( all)?( of)?( our| the| my)?( software| saas)? (subscriptions?|tools|licen[cs]es)\b", re.I)
CAPABILITIES = ("I could not match that to something I can look up, but here is what I can help with:\n"
                "- questions about company rules, with sources\n"
                "- spending: \"How much did we spend on GitHub last month?\", \"What is our biggest expense?\"\n"
                "- income and profit: \"What was our income in March?\"\n"
                "- tasks: \"How many tasks are blocked?\", \"Which tasks are overdue?\"\n"
                "- reports: \"Give me the monthly report\"\n"
                "- subscriptions: \"Which tools are unused?\", \"What does Datadog cost?\", \"Which subscriptions can we cancel?\"")


@dataclass(frozen=True)
class LookupAnswer:
    """Text for the user plus the numbers behind it."""

    text: str
    data: dict[str, Any]


def lookup_kind(question: str, frames: Frames | None = None) -> str | None:
    """Which computed lookup a question needs, or ``None``.

    Order matters: spending questions first ("how much did we spend on GitHub"), then facts about a
    named tool ("how many people use Slack"), then the other lookups.
    """
    if is_spending_question(question):
        return "spending"
    if frames is not None and tool_in_question(question, frames) and FACT_WORDS.search(question) \
            and not SAVINGS_WORDS.search(question):
        return "tool_facts"
    if INVENTORY.search(question) and not SAVINGS_WORDS.search(question) and not topic_of(question):
        return "inventory"
    if HEADCOUNT.search(question):
        return "headcount"
    if RANKING_WORDS.search(question) and MONEY_WORDS.search(question):
        return "ranking"
    if TASK_WORDS.search(question) and (any(re.search(p, question, re.I) for p in STATUS_WORDS.values())
                                        or re.search(r"\bhow many\b|\b(which|list|show|what)\b", question, re.I)):
        return "tasks"
    if INCOME_WORDS.search(question) and not REPORT_WORDS.search(question) \
            and re.search(r"\b(what|how much|show|tell|was|were|is|are)\b", question, re.I):
        return "income"
    return None


def tool_in_question(question: str, frames: Frames) -> str | None:
    """The listed subscription tool named in the question (longest name wins)."""
    hits = [t for t in frames.subscriptions["tool"]
            if re.search(rf"(?<![a-z0-9]){re.escape(t.lower())}(?![a-z0-9])", question.lower())]
    return max(hits, key=len) if hits else None


ACTION_VERBS = re.compile(r"\b(cancel|unsubscribe|terminate|end|remove|delete|downgrade|upgrade|renew|buy|purchase|"
                          r"stop paying for|get rid of)\b", re.I)
REQUEST_FORM = re.compile(r"^\s*(please\s+)?(cancel|unsubscribe|terminate|end|remove|delete|stop|downgrade|upgrade|renew|"
                          r"buy|purchase)\b|\b(i want you to|i need you to|please|go ahead and|can you|could you|"
                          r"would you|just)\b", re.I)
ADVICE_FORM = re.compile(r"^\s*(should|which|what|why|how|is|are|does|do|would it)\b", re.I)


def action_request_tool(question: str, frames: Frames | None) -> str | None:
    """The listed tool when the user orders the assistant to do something to it ("cancel Notion").

    The assistant never changes subscriptions, so such a message must be answered with that fact, not with the
    generic savings list. Advice questions ("Should we cancel Zoom?") are not orders and return ``None``.
    """
    if frames is None or not ACTION_VERBS.search(question) or ADVICE_FORM.search(question) \
            or not REQUEST_FORM.search(question):
        return None
    return tool_in_question(question, frames)


def _months(frames: Frames) -> list[str]:
    return sorted(frames.transactions["month"].unique())


# ------------------------------------------------------------------ income, profit
def answer_income(frames: Frames, question: str) -> LookupAnswer:
    """Income, expenses and net for the period in the question (default: last month)."""
    months = _months(frames)
    try:
        parsed = parse_months(question, months)
    except ValueError as exc:
        return LookupAnswer(str(exc), {"error": str(exc)})
    wanted, label = parsed if parsed else ([months[-1]], months[-1])
    tx = frames.transactions[frames.transactions["month"].isin(wanted)]
    income = round(float(tx.loc[tx["amount"] > 0, "amount"].sum()), 2)
    expenses = round(float(-tx.loc[tx["amount"] < 0, "amount"].sum()), 2)
    net = round(income - expenses, 2)
    data: dict[str, Any] = {"period_months": wanted, "income": income, "expenses": expenses, "net": net}
    text = (f"In {label}: income {eur(income)}, expenses {eur(expenses)}, net "
            f"{'profit' if net >= 0 else 'loss'} {eur(abs(net))}.")
    if len(wanted) > 1:
        by = tx.groupby("month")["amount"].agg(income=lambda s: s[s > 0].sum(), expenses=lambda s: -s[s < 0].sum())
        text += "\n" + "\n".join(f"- {m}: income {eur(r.income)}, expenses {eur(r.expenses)}, "
                                 f"net {eur(r.income - r.expenses)}" for m, r in by.iterrows())
        data["by_month"] = {m: round(float(r.income - r.expenses), 2) for m, r in by.iterrows()}
    return LookupAnswer(text, data)


# ------------------------------------------------------------------ rankings
def answer_ranking(frames: Frames, question: str) -> LookupAnswer:
    """Largest expense categories (or vendors) for the period (default: the whole data)."""
    months = _months(frames)
    try:
        parsed = parse_months(question, months)
    except ValueError as exc:
        return LookupAnswer(str(exc), {"error": str(exc)})
    wanted, label = parsed if parsed else (months, f"the last {len(months)} months ({months[0]} to {months[-1]})")
    tx = frames.transactions
    spent = tx[(tx["amount"] < 0) & tx["month"].isin(wanted)].copy()
    spent["eur"] = spent["amount"].abs()
    by_vendor = bool(re.search(r"\b(vendors?|suppliers?|tools?|counterpart\w*)\b", question, re.I))
    column = "counterparty" if by_vendor else "category"
    ranked = spent.groupby(column)["eur"].sum().sort_values(ascending=False)
    total = float(ranked.sum())
    top = ranked.head(5)
    lines = [f"- {name}: {eur(v)} ({v / total:.0%})" for name, v in top.items()]
    text = (f"Largest expense {'vendors' if by_vendor else 'categories'} in {label} (total {eur(total)}):\n"
            + "\n".join(lines))
    return LookupAnswer(text, {"by": column, "period_months": wanted, "total_eur": round(total, 2),
                               "top": {k: round(float(v), 2) for k, v in top.items()}})


# ------------------------------------------------------------------ headcount
def answer_headcount(frames: Frames) -> LookupAnswer:
    """Number of employees and how they split by job title."""
    emp = frames.employees
    counts = emp["job_title"].value_counts()
    text = (f"Nexora has {len(emp)} employees: "
            + ", ".join(f"{n} {t}" for t, n in counts.items()) + ".")
    return LookupAnswer(text, {"employees": len(emp), "by_job_title": counts.to_dict()})


# ------------------------------------------------------------------ tasks
def answer_tasks(frames: Frames, question: str, employee_id: str | None = None) -> LookupAnswer:
    """Task counts or a list, by status and project. ``employee_id`` limits the answer to one person's tasks."""
    tasks = frames.tasks.copy()
    scope = "all projects"
    if employee_id is not None:
        tasks, scope = tasks[tasks["owner"] == employee_id], "your tasks"
    for project in sorted(set(frames.tasks["project"]), key=len, reverse=True):
        if re.search(rf"\b{re.escape(project.lower())}\b", question.lower()):
            tasks, scope = tasks[tasks["project"] == project], project
            break
    today = pd.Period(_months(frames)[-1], "M").end_time.normalize()  # end of the last month, as in the reports
    status = next((s for s, p in STATUS_WORDS.items() if re.search(p, question, re.I)), None)
    if status == "overdue":
        chosen = tasks[(tasks["status"] != "done") & (tasks["due"] < today)]
        label = "overdue (not done, due before " + today.date().isoformat() + ")"
    elif status:
        chosen, label = tasks[tasks["status"] == status], status.replace("_", " ")
    else:
        chosen, label = tasks, "in total"
    names = dict(zip(frames.employees["employee_id"], frames.employees["name"], strict=True))
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    listing = sorted(chosen.itertuples(), key=lambda r: (order[r.priority], r.due))
    text = f"{len(chosen)} tasks are {label} ({scope}, {len(tasks)} tasks)."
    if status is None:
        counts = tasks["status"].value_counts()
        text = f"{len(tasks)} tasks ({scope}): " + ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in counts.items()) + "."
    elif len(chosen) == 1:
        text = text.replace("tasks are", "task is")
    wants_list = bool(re.search(r"\b(which|list|show|what are|who)\b", question, re.I)) and status is not None
    if wants_list and listing:
        text += "\n" + "\n".join(
            f"- {r.task_id} [{r.priority}] {r.project}: {r.title} (owner {names.get(r.owner, r.owner)}, "
            f"due {r.due.date().isoformat()})" for r in listing[:MAX_LISTED])
        if len(listing) > MAX_LISTED:
            text += f"\n(+{len(listing) - MAX_LISTED} more, most urgent first)"
    return LookupAnswer(text, {"status": status, "count": len(chosen), "scope": scope, "total": len(tasks),
                               "task_ids": [r.task_id for r in listing]})


# ------------------------------------------------------------------ subscriptions
def answer_tool_facts(frames: Frames, cfg: Config, question: str, flags: list[Flag]) -> LookupAnswer:
    """Plan, cost, seats, active users and owner of one subscription (from the data, not estimated)."""
    tool = tool_in_question(question, frames)
    sub = frames.subscriptions[frames.subscriptions["tool"] == tool].iloc[0]
    usage = frames.usage_log[frames.usage_log["tool"] == tool].sort_values("month")
    charges = frames.software_charges(cfg)
    last = charges[charges["counterparty"] == tool].sort_values("date").tail(1)
    latest_usage = usage.iloc[-1]
    text = (f"{tool} ({sub['plan']}, {sub['pricing_model'].replace('_', ' ')}): list price {eur(float(sub['monthly_cost']))}"
            f" per month")
    if not last.empty:
        text += f"; last charge {eur(abs(float(last['amount'].iloc[0])))} on {last['date'].iloc[0].date().isoformat()}"
    text += (f". {int(latest_usage['active_users'])} of {int(sub['seats'])} seats active in {latest_usage['month']}; "
             f"owner {sub['owner']}; billed on day {int(sub['billing_day'])}.")
    mine = [f for f in flags if tool in f.tool.split("+")]
    if mine:
        text += "\nFlagged: " + "; ".join(f.detail for f in mine) + "."
    return LookupAnswer(text, {"tool": tool, "monthly_cost": float(sub["monthly_cost"]), "seats": int(sub["seats"]),
                               "active_users": int(latest_usage["active_users"]), "month": latest_usage["month"]})


def answer_inventory(frames: Frames) -> LookupAnswer:
    """How many subscriptions the company has, what they cost in total and the most expensive ones."""
    subs = frames.subscriptions.sort_values("monthly_cost", ascending=False)
    total = float(subs["monthly_cost"].sum())
    top = subs.head(5)
    lines = [f"- {r.tool} ({r.plan}): {eur(float(r.monthly_cost))}/month, {int(r.seats)} seats"
             for r in top.itertuples()]
    text = (f"We have {len(subs)} software subscriptions, costing {eur(total)} per month list price "
            f"({eur(total * 12)} per year). The most expensive:\n" + "\n".join(lines)
            + (f"\n(+{len(subs) - len(top)} more on the Dashboard)" if len(subs) > len(top) else ""))
    return LookupAnswer(text, {"count": len(subs), "monthly_total": round(total, 2),
                               "tools": [{"tool": r.tool, "monthly_cost": float(r.monthly_cost),
                                          "seats": int(r.seats)} for r in subs.itertuples()]})


def topic_of(question: str) -> str | None:
    """Which kind of subscription problem the question asks about (``None`` = all)."""
    return next((kind for kind, pattern in TOPICS.items() if re.search(pattern, question, re.I)), None)
