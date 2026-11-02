"""Overlapping tools: two tools from the same configured category, both billed and in use."""

from __future__ import annotations

from itertools import combinations

from nexora.config import Config
from nexora.db.repositories import Frames
from nexora.detectors.base import Flag


def detect_overlap(frames: Frames, cfg: Config) -> list[Flag]:
    """Flag each tool pair of a category whose tools are both billed with active users.

    The key month is the first month in which both are billed and active. The saving is the
    monthly cost of the cheaper tool.
    """
    charges = frames.software_charges(cfg)
    billed = charges.groupby("counterparty")["month"].agg(set).to_dict()
    in_use = frames.usage_log[frames.usage_log["active_users"] > 0]
    active = in_use.groupby("tool")["month"].agg(set).to_dict()
    subs = frames.subscriptions
    annual = set(subs.loc[subs["pricing_model"] == "annual", "tool"])
    listed = set(subs["tool"])
    monthly = dict(zip(subs["tool"], subs["monthly_cost"], strict=True))
    latest = charges.sort_values("date").groupby("counterparty")["amount"].last().abs().to_dict()
    flags = []
    for tools in cfg.detectors.overlap_categories.values():
        for a, b in combinations([t for t in tools if t in listed], 2):
            both = billed.get(a, set()) & billed.get(b, set()) & active.get(a, set()) & active.get(b, set())
            if not both:
                continue
            cost = min(monthly[t] if t in annual else latest.get(t, monthly[t]) for t in (a, b))
            flags.append(Flag("overlap", "+".join(sorted((a, b))), min(both), monthly_saving=round(cost, 2),
                              detail=f"{a} and {b} overlap; the cheaper one costs {cost:.2f} EUR/month"))
    return flags
