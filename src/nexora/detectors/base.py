"""Shared result type for all detectors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Flag:
    """One suspected problem. ``detection_key`` matches ground_truth.json's key format.

    ``monthly_saving`` is the recurring EUR per month that fixing it would save;
    ``one_off_recovery`` is a single refundable amount (duplicate charges).
    """

    type: str
    tool: str
    month: str
    txn_ids: tuple[str, ...] = ()
    monthly_saving: float = 0.0
    one_off_recovery: float = 0.0
    detail: str = ""

    @property
    def detection_key(self) -> str:
        """``<type>|<tool>|<YYYY-MM>``."""
        return f"{self.type}|{self.tool}|{self.month}"
