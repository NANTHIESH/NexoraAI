"""Unexplained price increases on fixed-price subscriptions."""

from __future__ import annotations

import pandas as pd

from nexora.config import Config
from nexora.db.repositories import Frames
from nexora.detectors.base import Flag


def _consecutive(a: str, b: str) -> bool:
    return pd.Period(b, "M") == pd.Period(a, "M") + 1


def detect_price_changes(frames: Frames, cfg: Config) -> list[Flag]:
    """Flag month-over-month increases above the threshold unless a plan change is recorded.

    Only subscriptions with ``pricing_model == fixed`` are checked (usage-based and annual
    tools legitimately vary). The first charge of each month is compared, so a duplicate
    charge does not look like a price change.
    """
    threshold = cfg.detectors.price_increase_threshold
    fixed = set(frames.subscriptions.loc[frames.subscriptions["pricing_model"] == "fixed", "tool"])
    recorded = {(r.tool, r.month) for r in frames.plan_changes.itertuples()
                if r.change_type == "plan_change"}
    charges = frames.software_charges(cfg)
    charges = charges[charges["counterparty"].isin(fixed)].sort_values(["date", "txn_id"])
    first = charges.groupby(["counterparty", "month"], as_index=False).first()
    flags = []
    for tool, group in first.groupby("counterparty"):
        prev = None
        for row in group.sort_values("month").itertuples():
            if prev is not None and _consecutive(prev.month, row.month):
                before, now = abs(prev.amount), abs(row.amount)
                rise = (now - before) / before
                if rise > threshold and (tool, row.month) not in recorded:
                    flags.append(Flag("price_increase", tool, row.month, (row.txn_id,),
                                      monthly_saving=round(now - before, 2),
                                      detail=f"{tool} rose {rise * 100:.0f}% from {before:.2f} to {now:.2f} EUR"))
            prev = row
    return flags
