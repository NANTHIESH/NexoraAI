"""Subscriptions that are still billed but have no active users."""

from __future__ import annotations

import pandas as pd

from nexora.config import Config
from nexora.db.repositories import Frames
from nexora.detectors.base import Flag


def _next(month: str) -> str:
    return str(pd.Period(month, "M") + 1)


def detect_unused(frames: Frames, cfg: Config) -> list[Flag]:
    """Flag runs of ``unused_min_months`` or more consecutive billed months with 0 active users.

    The key month is the first month of the run. Monthly saving is only counted when the run
    is still ongoing in the last month of the data.
    """
    min_months = cfg.detectors.unused_min_months
    charges = frames.software_charges(cfg)
    billed = set(zip(charges["counterparty"], charges["month"], strict=True))
    subs = frames.subscriptions
    annual = set(subs.loc[subs["pricing_model"] == "annual", "tool"])
    monthly_cost = dict(zip(subs["tool"], subs["monthly_cost"], strict=True))
    last_charge = charges.sort_values("date").groupby("counterparty")["amount"].last().abs().to_dict()
    flags = []
    for tool, usage in frames.usage_log.groupby("tool"):
        usage = usage.sort_values("month")
        last_month = usage["month"].iloc[-1]
        run: list[str] = []
        for row in [*usage.itertuples(), None]:
            idle = row is not None and row.active_users == 0 and (tool in annual or (tool, row.month) in billed)
            if idle and (not run or _next(run[-1]) == row.month):
                run.append(row.month)
                continue
            if len(run) >= min_months:
                cost = last_charge.get(tool, monthly_cost[tool]) if run[-1] == last_month else 0.0
                flags.append(Flag("unused_subscription", tool, run[0], monthly_saving=round(cost, 2),
                                  detail=f"{tool}: 0 active users for {len(run)} months"))
            run = [row.month] if idle else []
    return flags
