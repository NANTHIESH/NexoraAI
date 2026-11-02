"""Rule-based detectors for subscription and billing problems."""

from __future__ import annotations

from nexora.config import Config, load_config
from nexora.db.repositories import Frames
from nexora.detectors.base import Flag
from nexora.detectors.duplicates import detect_duplicates
from nexora.detectors.overlap import detect_overlap
from nexora.detectors.price_change import detect_price_changes
from nexora.detectors.unlisted import detect_unlisted
from nexora.detectors.unused import detect_unused

DETECTORS = (detect_duplicates, detect_price_changes, detect_unused, detect_unlisted, detect_overlap)


def run_all(frames: Frames, cfg: Config | None = None) -> list[Flag]:
    """Run every detector; flags are sorted by detection key."""
    cfg = cfg or load_config()
    flags = [flag for detector in DETECTORS for flag in detector(frames, cfg)]
    return sorted(flags, key=lambda f: f.detection_key)


__all__ = ["DETECTORS", "Flag", "run_all"]
