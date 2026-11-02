"""Recurring software charges from vendors that are not in the subscription list."""

from __future__ import annotations

from nexora.config import Config
from nexora.db.repositories import Frames
from nexora.detectors.base import Flag


def detect_unlisted(frames: Frames, cfg: Config) -> list[Flag]:
    """Flag software counterparties absent from subscriptions that bill in 3+ distinct months."""
    listed = set(frames.subscriptions["tool"])
    minimum = cfg.detectors.recurring_min_months
    charges = frames.software_charges(cfg)
    flags = []
    for vendor, group in charges[~charges["counterparty"].isin(listed)].groupby("counterparty"):
        months = sorted(group["month"].unique())
        if len(months) >= minimum:
            avg = round(group["amount"].abs().mean(), 2)
            flags.append(Flag("unlisted_charge", vendor, months[0], tuple(sorted(group["txn_id"])),
                              monthly_saving=avg,
                              detail=f"{vendor} bills about {avg:.2f} EUR/month and is not in the subscription list"))
    return flags
