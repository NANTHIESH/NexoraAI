"""Subscription agent: runs all detectors and turns flags into savings recommendations.

Recommendations only. Nothing is cancelled or changed automatically: every item carries
``requires_confirmation=True`` and the UI must ask a human before any action.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nexora.config import Config, load_config
from nexora.db.repositories import Frames
from nexora.detectors import Flag, run_all


@dataclass(frozen=True)
class Recommendation:
    """A suggested action with its estimated saving."""

    flag_type: str
    tool: str
    month: str
    action: str
    text: str
    monthly_saving: float
    one_off_recovery: float
    requires_confirmation: bool = True


@dataclass(frozen=True)
class SubscriptionAnalysis:
    """All flags, recommendations and estimated savings (EUR)."""

    flags: list[Flag]
    recommendations: list[Recommendation]
    monthly_savings: float
    yearly_savings: float
    one_off_recovery: float
    savings_by_tool: dict[str, float] = field(default_factory=dict)


def _recommend(flag: Flag) -> Recommendation:
    saving, recovery = flag.monthly_saving, flag.one_off_recovery
    if flag.type == "unused_subscription":
        action = "cancel"
        text = f"Cancel {flag.tool}: {flag.detail.split(': ', 1)[1]}, saves {saving:.0f} EUR/month"
    elif flag.type == "duplicate_charge":
        action = "request_refund"
        text = f"Ask {flag.tool} for a refund of {recovery:.2f} EUR: it was charged twice in {flag.month}"
    elif flag.type == "price_increase":
        action = "negotiate"
        text = f"Review {flag.tool}: {flag.detail.split(' rose ', 1)[1].split(' from')[0]} price rise from " \
               f"{flag.month} (+{saving:.2f} EUR/month). Negotiate or switch plan"
    elif flag.type == "unlisted_charge":
        action = "register_or_cancel"
        text = (f"Register or cancel {flag.tool}: it bills about {saving:.0f} EUR/month "
                "but is not in the subscription list")
    else:
        action = "consolidate"
        pair = flag.tool.replace("+", " and ")
        text = f"Consolidate {pair}: overlapping tools, dropping the cheaper one saves {saving:.0f} EUR/month"
    return Recommendation(flag.type, flag.tool, flag.month, action, text, saving, recovery)


def _tool_savings(flags: list[Flag]) -> dict[str, float]:
    """Monthly saving per tool without double counting.

    A tool flagged as unused is cancelled entirely, so its price-increase saving is not added.
    Overlap flags name two tools; their saving is attributed to the pair.
    """
    unused = {f.tool for f in flags if f.type == "unused_subscription"}
    savings: dict[str, float] = {}
    for f in flags:
        if f.type == "price_increase" and f.tool in unused:
            continue
        savings[f.tool] = round(savings.get(f.tool, 0.0) + f.monthly_saving, 2)
    return savings


def analyze_subscriptions(frames: Frames, cfg: Config | None = None) -> SubscriptionAnalysis:
    """Run all detectors and estimate monthly/yearly savings."""
    cfg = cfg or load_config()
    flags = run_all(frames, cfg)
    by_tool = _tool_savings(flags)
    monthly = round(sum(by_tool.values()), 2)
    return SubscriptionAnalysis(
        flags=flags, recommendations=[_recommend(f) for f in flags], monthly_savings=monthly,
        yearly_savings=round(monthly * 12, 2),
        one_off_recovery=round(sum(f.one_off_recovery for f in flags), 2), savings_by_tool=by_tool)
