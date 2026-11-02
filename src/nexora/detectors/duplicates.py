"""Duplicate charges: same counterparty, same month, similar amounts a few days apart."""

from __future__ import annotations

from itertools import combinations

from nexora.config import Config
from nexora.db.repositories import Frames
from nexora.detectors.base import Flag


def detect_duplicates(frames: Frames, cfg: Config) -> list[Flag]:
    """Flag a tool-month with 2+ charges within the window whose amounts differ by < tolerance."""
    window = cfg.detectors.duplicate_window_days
    tolerance = cfg.detectors.duplicate_amount_tolerance
    flags = []
    for (tool, month), group in frames.software_charges(cfg).groupby(["counterparty", "month"]):
        rows = list(group.sort_values(["date", "txn_id"]).itertuples())
        ids: set[str] = set()
        recovery = 0.0
        for a, b in combinations(rows, 2):
            high = max(abs(a.amount), abs(b.amount))
            close = abs(abs(a.amount) - abs(b.amount)) / high < tolerance
            if (b.date - a.date).days <= window and close:
                ids |= {a.txn_id, b.txn_id}
                recovery = max(recovery, round(min(abs(a.amount), abs(b.amount)), 2))
        if ids:
            flags.append(Flag("duplicate_charge", tool, month, tuple(sorted(ids)),
                              one_off_recovery=recovery,
                              detail=f"{tool} was charged twice in {month} (about {recovery:.2f} EUR)"))
    return flags
