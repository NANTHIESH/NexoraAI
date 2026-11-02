"""Spending questions: "How much did we spend on GitHub last month?" answered from the transactions.

Python finds the target (a tool, vendor, category or "subscriptions"), the period and the amounts;
the language model is not involved, so every number is computed. Part of the report agent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pandas as pd

from nexora.config import Config
from nexora.db.repositories import Frames

SPEND_QUESTION = re.compile(
    r"\bhow much\b.*\b(spen[dt]|spending|cost|costs|costing|paid|pay|paying|charged|billed|bill)\b"
    r"|\b(spen[dt]|spending)\b.*\b(on|for)\b|\bwhat (did|do|have) we (spend|spent|pay|paid)\b"
    r"|\bcosts? (of|for)\b|\bwhat (was|were|is|are)\b.*\b(spent|paid|costs?)\b", re.IGNORECASE)
# "How much can we/I spend on training?" is a policy question, but "can you tell me how much we spent" is not.
POLICY_WORDS = re.compile(
    r"\b(can|may|could|should) (i|we|employees?|staff|developers?|people)\b"
    r"|\b(allowed|limit|budget|approval|approve|policy|rule)\b", re.IGNORECASE)
FIRST_PERSON = re.compile(r"\b(i|my|me|mine)\b", re.IGNORECASE)
WE_WORDS = re.compile(r"\b(we|our|us|company|nexora|office)\b", re.IGNORECASE)
SUBSCRIPTION_WORDS = re.compile(r"\b(subscriptions?|saas|software tools?|tools|licen[cs]es?)\b", re.IGNORECASE)
MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
MONTH_NAME = re.compile(r"\b(" + "|".join(m[:3] + (r"\w*" if len(m) > 3 else "") for m in MONTHS) + r")\b"
                        r"(?:\s+(20\d\d))?", re.IGNORECASE)
ISO_MONTH = re.compile(r"\b(20\d\d)-(\d\d)\b")
LAST_MONTH = re.compile(r"\b(last|previous|past|this|current) month\b", re.IGNORECASE)
WHOLE_YEAR = re.compile(r"\b(year|annual|annually|12 months|twelve months|ytd|overall|in total)\b", re.IGNORECASE)
QUARTER = re.compile(r"\b(last|past) (quarter|3 months|three months)\b", re.IGNORECASE)
HALF_YEAR = re.compile(r"\b(last|past) (6 months|six months)\b", re.IGNORECASE)
UNKNOWN_OBJECT = re.compile(
    r"\b(?:on|for)\s+(?:the\s+)?(?!last\b|this\b|all\b|everything\b|total\b|in\b|each\b|every\b|past\b|previous\b|"
    r"whole\b|entire\b|full\b|year\b|month\b|quarter\b|\d)\w+")
PAID_LEAVE = re.compile(r"\bpaid (time off|leave|vacation|holiday|holidays|sick|days?|parental)\b", re.IGNORECASE)
WEAK_VERBS = re.compile(r"\b(pay|paying|paid)\b", re.IGNORECASE)
STRONG_VERBS = re.compile(r"\b(spen[dt]|spending|costs?|costing|charged|billed)\b", re.IGNORECASE)
MAX_LISTED_CHARGES = 6
MAX_LISTED_TOOLS = 5
SUGGESTION = ("I could not tell what to add up. Try: \"How much did we spend on GitHub last month?\", "
              "\"How much did we spend on subscriptions in total?\" or \"How much did we spend on travel in March?\"")


@dataclass(frozen=True)
class SpendingAnswer:
    """Text for the user plus the numbers behind it."""

    text: str
    data: dict[str, Any]


def is_spending_question(question: str) -> bool:
    """True for "how much did we spend/pay on ..." questions about company money (not policy questions)."""
    if not SPEND_QUESTION.search(question) or POLICY_WORDS.search(question) or PAID_LEAVE.search(question):
        return False
    has_we = bool(WE_WORDS.search(question))
    if FIRST_PERSON.search(question) and not has_we:
        return False  # "How much do I get paid?" is about a person, not the company
    # "pay"/"paid" alone is too weak ("how much paid ..."): it needs a company subject like "we" or "our"
    return has_we or bool(STRONG_VERBS.search(question))


def eur(amount: float) -> str:
    """``1234.5`` -> ``1,234.50 EUR``."""
    return f"{amount:,.2f} EUR"


# ------------------------------------------------------------------ period
def parse_months(question: str, months: list[str]) -> tuple[list[str], str] | None:
    """Months (``YYYY-MM``) the question refers to and a label; ``None`` means "no period given".

    "Last/this month" means the latest month in the data. A month name without a year picks its most
    recent occurrence. Raises ``ValueError`` with a helpful message when the month is not in the data.
    """
    if iso := ISO_MONTH.search(question):
        wanted = f"{iso.group(1)}-{iso.group(2)}"
        return _require([wanted], months, wanted)
    if match := MONTH_NAME.search(question):
        number = MONTHS[next(m for m in MONTHS if m.startswith(match.group(1).lower()[:3]))]
        year = match.group(2)
        candidates = [m for m in months if int(m[5:]) == number and (not year or m[:4] == year)]
        if not candidates:
            raise ValueError(f"I have no data for that month. My data covers {months[0]} to {months[-1]}.")
        return [candidates[-1]], candidates[-1]
    if QUARTER.search(question):
        return months[-3:], f"the last 3 months ({months[-3]} to {months[-1]})"
    if HALF_YEAR.search(question):
        return months[-6:], f"the last 6 months ({months[-6]} to {months[-1]})"
    if LAST_MONTH.search(question):
        return [months[-1]], months[-1]
    if WHOLE_YEAR.search(question):
        return months, f"the last {len(months)} months ({months[0]} to {months[-1]})"
    return None


def _require(wanted: list[str], months: list[str], label: str) -> tuple[list[str], str]:
    if not set(wanted) <= set(months):
        raise ValueError(f"I have no data for {label}. My data covers {months[0]} to {months[-1]}.")
    return wanted, label


# ------------------------------------------------------------------ target
def find_target(question: str, frames: Frames, cfg: Config) -> tuple[str, pd.Series] | None:
    """(label, row mask) for the thing to add up, or ``None`` if the question does not name one."""
    tx = frames.transactions
    spend = tx["amount"] < 0
    names = {n.lower(): n for n in set(frames.subscriptions["tool"]) | set(tx.loc[spend, "counterparty"])}
    categories = {c.lower(): c for c in tx.loc[spend, "category"].unique()}
    text = question.lower()
    found = [(len(n), "vendor", n) for n in names if re.search(rf"(?<![a-z0-9]){re.escape(n)}(?![a-z0-9])", text)]
    if found:
        _, _, key = max(found)
        return names[key], spend & (tx["counterparty"].str.lower() == key)
    if SUBSCRIPTION_WORDS.search(question):
        listed = set(frames.subscriptions["tool"])
        return "subscriptions (tools on the subscription list)", spend & tx["counterparty"].isin(listed)
    for key, category in sorted(categories.items(), key=lambda kv: -len(kv[0])):
        if re.search(rf"\b{re.escape(key)}s?\b", text):
            return f"{category} (category)", spend & (tx["category"] == category)
    if UNKNOWN_OBJECT.search(text):
        return None  # "spent on <something I do not know>": better to say so than to add up everything
    return "all expenses", spend


# ------------------------------------------------------------------ answer
def charge_lines(charges: list[dict[str, Any]]) -> str:
    """Bullet list of the largest charges (shown by date), with a note when some are left out."""
    shown = sorted(sorted(charges, key=lambda c: -c["amount_eur"])[:MAX_LISTED_CHARGES], key=lambda c: c["date"])
    lines = [f"- {c['date']}: {eur(c['amount_eur'])} {c['description']}" for c in shown]
    if len(charges) > len(shown):
        lines.append(f"(+{len(charges) - len(shown)} smaller charges; all are in the data)")
    return "\n".join(lines)


def answer_spending(frames: Frames, cfg: Config, question: str) -> SpendingAnswer:
    """Answer a spending question with computed numbers (or a clear message when it cannot)."""
    tx = frames.transactions
    months = sorted(tx["month"].unique())
    try:
        period = parse_months(question, months)
    except ValueError as exc:
        return SpendingAnswer(str(exc), {"error": str(exc)})
    target = find_target(question, frames, cfg)
    if target is None:
        return SpendingAnswer(SUGGESTION, {"error": "no target"})
    label, mask = target
    spent = tx[mask].copy()
    spent["eur"] = spent["amount"].abs()
    monthly = spent.groupby("month")["eur"].sum()
    wanted, period_label = period if period else ([months[-1]], months[-1])
    inside = spent[spent["month"].isin(wanted)]
    total = round(float(inside["eur"].sum()), 2)
    data: dict[str, Any] = {"target": label, "period_months": wanted, "total_eur": total,
                            "charges": [{"txn_id": r.txn_id, "date": r.date.date().isoformat(),
                                         "description": r.description, "amount_eur": round(r.eur, 2)}
                                        for r in inside.sort_values("date").itertuples()]}
    year_total = round(float(monthly.sum()), 2)
    if period is None:  # no period given: lead with last month AND the whole year
        data["year_total_eur"] = year_total
        text = (f"We spent {eur(total)} on {label} last month ({period_label}) and {eur(year_total)} over the "
                f"last {len(months)} months ({months[0]} to {months[-1]}, average "
                f"{eur(year_total / len(months))} per month).")
        if len(inside) > 1:
            text += "\nLast month's charges:\n" + charge_lines(data["charges"])
    elif inside.empty:
        text = f"No spending on {label} in {period_label}."
    elif len(wanted) == 1:
        text = f"We spent {eur(total)} on {label} in {period_label}"
        n = len(inside)
        text += f" ({n} charge{'s' if n != 1 else ''})." if n > 1 else f" (1 charge on {data['charges'][0]['date']})."
        if n > 1:
            text += "\n" + charge_lines(data["charges"])
    else:
        by_month = inside.groupby("month")["eur"].sum()
        text = (f"We spent {eur(total)} on {label} in {period_label} "
                f"(average {eur(total / len(wanted))} per month).")
        text += "\n" + "\n".join(f"- {m}: {eur(v)}" for m, v in by_month.items())
        data["by_month"] = {m: round(float(v), 2) for m, v in by_month.items()}
    if label.startswith("subscriptions") and not inside.empty:
        top = inside.groupby("counterparty")["eur"].sum().sort_values(ascending=False).head(MAX_LISTED_TOOLS)
        text += "\nLargest: " + ", ".join(f"{t} {eur(v)}" for t, v in top.items()) + "."
        listed = set(frames.subscriptions["tool"])
        sw = tx[tx["category"].isin(cfg.detectors.software_categories) & (tx["amount"] < 0)
                & ~tx["counterparty"].isin(listed) & tx["month"].isin(wanted)]
        if not sw.empty:
            text += (f"\nNot included: {eur(float(sw['amount'].abs().sum()))} of software charges from tools that "
                     f"are not on the subscription list ({', '.join(sorted(sw['counterparty'].unique()))}).")
            data["unlisted_software_eur"] = round(float(sw["amount"].abs().sum()), 2)
    return SpendingAnswer(text, data)
