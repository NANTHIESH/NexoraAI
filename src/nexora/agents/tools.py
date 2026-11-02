"""Small helpers shared by the agents."""

from __future__ import annotations

import re

PERIODS = ("weekly", "monthly", "yearly")
_PERIOD_WORDS = {"weekly": r"\b(week|weekly)\b", "yearly": r"\b(year|yearly|annual|annually)\b",
                 "monthly": r"\b(month|monthly)\b"}


def format_eur(amount: float) -> str:
    """``12345.6`` -> ``12,346 EUR`` (whole euros for readability)."""
    return f"{amount:,.0f} EUR"


def parse_period(question: str, default: str = "monthly") -> str:
    """Pick weekly/monthly/yearly from free text; ``default`` when none is mentioned."""
    for period in ("weekly", "yearly", "monthly"):
        if re.search(_PERIOD_WORDS[period], question, re.IGNORECASE):
            return period
    return default
