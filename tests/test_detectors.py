"""Phase 4 tests: each detector on small hand-made data, and all five on seeded synthetic data."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest
from scripts.generate_data import generate

from nexora.config import load_config
from nexora.db.repositories import Frames, frames_from_csv
from nexora.detectors import run_all
from nexora.detectors.duplicates import detect_duplicates
from nexora.detectors.overlap import detect_overlap
from nexora.detectors.price_change import detect_price_changes
from nexora.detectors.unlisted import detect_unlisted
from nexora.detectors.unused import detect_unused

CFG = load_config()
SEEDS = (42, 7, 123)


def make_frames(charges=(), subs=(), usage=(), plan_changes=()) -> Frames:
    """charges: (tool, date, amount); subs: (tool, pricing, monthly_cost); usage: (tool, month, users)."""
    tx = pd.DataFrame([{"txn_id": f"T{i:03d}", "date": pd.Timestamp(d), "description": "x",
                        "category": "Software", "counterparty": t, "amount": -a, "balance": 0.0}
                       for i, (t, d, a) in enumerate(charges)],
                      columns=["txn_id", "date", "description", "category", "counterparty", "amount", "balance"])
    tx["date"] = pd.to_datetime(tx["date"])
    tx["month"] = tx["date"].dt.strftime("%Y-%m")
    subscriptions = pd.DataFrame([{"tool": t, "pricing_model": p, "monthly_cost": c} for t, p, c in subs],
                                 columns=["tool", "pricing_model", "monthly_cost"])
    usage_log = pd.DataFrame([{"tool": t, "month": m, "active_users": u} for t, m, u in usage],
                             columns=["tool", "month", "active_users"])
    changes = pd.DataFrame(list(plan_changes), columns=["tool", "month", "change_type"])
    empty = pd.DataFrame()
    return Frames(empty, tx, subscriptions, usage_log, changes, empty)


# ------------------------------------------------------------------ duplicates
def test_duplicate_flagged_when_close_in_time_and_amount() -> None:
    f = make_frames([("A", "2026-01-03", 100.0), ("A", "2026-01-05", 101.0)])
    flags = detect_duplicates(f, CFG)
    assert [x.detection_key for x in flags] == ["duplicate_charge|A|2026-01"]
    assert flags[0].one_off_recovery == 100.0


@pytest.mark.parametrize(("second_day", "second_amount"), [(12, 100.0), (5, 103.0), (5, 7.25)])
def test_duplicate_not_flagged_when_far_apart_or_different(second_day: int, second_amount: float) -> None:
    f = make_frames([("A", "2026-01-03", 100.0), ("A", f"2026-01-{second_day:02d}", second_amount)])
    assert detect_duplicates(f, CFG) == []


def test_duplicate_ignores_charges_in_different_months() -> None:
    assert detect_duplicates(make_frames([("A", "2026-01-30", 50.0), ("A", "2026-02-02", 50.0)]), CFG) == []


# ------------------------------------------------------------------ price changes
def price_frames(second: float, pricing: str = "fixed", changes=()) -> Frames:
    return make_frames([("A", "2026-01-05", 100.0), ("A", "2026-02-05", second)],
                       subs=[("A", pricing, 100.0)], plan_changes=changes)


def test_price_increase_above_threshold_is_flagged() -> None:
    flags = detect_price_changes(price_frames(115.0), CFG)
    assert [x.detection_key for x in flags] == ["price_increase|A|2026-02"]
    assert flags[0].monthly_saving == 15.0


@pytest.mark.parametrize("second", [100.0, 109.0, 90.0])
def test_small_or_negative_changes_are_not_flagged(second: float) -> None:
    assert detect_price_changes(price_frames(second), CFG) == []


def test_recorded_plan_change_is_not_flagged() -> None:
    assert detect_price_changes(price_frames(150.0, changes=[("A", "2026-02", "plan_change")]), CFG) == []


@pytest.mark.parametrize("pricing", ["usage_based", "annual"])
def test_only_fixed_price_tools_are_checked(pricing: str) -> None:
    assert detect_price_changes(price_frames(300.0, pricing), CFG) == []


def test_duplicate_charge_does_not_look_like_a_price_increase() -> None:
    f = make_frames([("A", "2026-01-05", 100.0), ("A", "2026-02-05", 100.0), ("A", "2026-02-07", 100.0)],
                    subs=[("A", "fixed", 100.0)])
    assert detect_price_changes(f, CFG) == []


# ------------------------------------------------------------------ unused
def unused_frames(users: list[int], billed: bool = True) -> Frames:
    months = [f"2026-{m:02d}" for m in range(1, len(users) + 1)]
    charges = [("A", f"{m}-05", 40.0) for m in months] if billed else []
    usage = [("A", m, u) for m, u in zip(months, users, strict=True)]
    return make_frames(charges, subs=[("A", "fixed", 40.0)], usage=usage)


def test_three_idle_billed_months_are_flagged_with_run_start() -> None:
    flags = detect_unused(unused_frames([5, 5, 0, 0, 0]), CFG)
    assert [x.detection_key for x in flags] == ["unused_subscription|A|2026-03"]
    assert flags[0].monthly_saving == 40.0  # run is ongoing


def test_two_idle_months_are_below_threshold() -> None:
    assert detect_unused(unused_frames([5, 0, 0, 5]), CFG) == []


def test_idle_but_not_billed_is_not_flagged() -> None:
    assert detect_unused(unused_frames([0, 0, 0, 0], billed=False), CFG) == []


def test_finished_idle_run_is_flagged_without_saving() -> None:
    flags = detect_unused(unused_frames([0, 0, 0, 4, 4]), CFG)
    assert flags[0].detection_key == "unused_subscription|A|2026-01" and flags[0].monthly_saving == 0.0


# ------------------------------------------------------------------ unlisted
def test_recurring_unlisted_vendor_is_flagged_after_three_months() -> None:
    charges = [("Canva", f"2026-0{m}-08", 52.0) for m in (1, 2, 3)]
    flags = detect_unlisted(make_frames(charges, subs=[("A", "fixed", 1.0)]), CFG)
    assert [x.detection_key for x in flags] == ["unlisted_charge|Canva|2026-01"]


def test_one_off_or_listed_vendors_are_not_flagged() -> None:
    f = make_frames([("Canva", "2026-01-08", 52.0), ("Canva", "2026-02-08", 52.0),
                     ("A", "2026-01-01", 1.0), ("A", "2026-02-01", 1.0), ("A", "2026-03-01", 1.0)],
                    subs=[("A", "fixed", 1.0)])
    assert detect_unlisted(f, CFG) == []


# ------------------------------------------------------------------ overlap
def overlap_frames(users_b: int) -> Frames:
    charges = [(t, f"2026-0{m}-05", c) for t, c in (("Jira", 116.25), ("Linear", 64.0)) for m in (2, 3)]
    usage = [("Jira", "2026-02", 9), ("Jira", "2026-03", 9), ("Linear", "2026-02", users_b),
             ("Linear", "2026-03", users_b)]
    return make_frames(charges, subs=[("Jira", "fixed", 116.25), ("Linear", "fixed", 64.0)], usage=usage)


def test_overlap_flagged_when_both_billed_and_used() -> None:
    flags = detect_overlap(overlap_frames(5), CFG)
    assert [x.detection_key for x in flags] == ["overlap|Jira+Linear|2026-02"]
    assert flags[0].monthly_saving == 64.0


def test_no_overlap_when_second_tool_has_no_users() -> None:
    assert detect_overlap(overlap_frames(0), CFG) == []


# ------------------------------------------------------------------ seeded synthetic data
@pytest.fixture(scope="module")
def runs(tmp_path_factory: pytest.TempPathFactory) -> dict[int, tuple[set[str], set[str], set[str]]]:
    out = {}
    for seed in SEEDS:
        folder: Path = tmp_path_factory.mktemp(f"d{seed}")
        generate(seed, folder)
        truth = json.loads((folder / "ground_truth.json").read_text(encoding="utf-8"))
        found = {f.detection_key for f in run_all(frames_from_csv(folder), CFG)}
        out[seed] = ({e["expected_detection_key"] for e in truth["errors"]}, found,
                     {d["would_be_false_key"] for d in truth["decoys"]})
    return out


@pytest.mark.parametrize("seed", SEEDS)
def test_all_planted_errors_are_found(runs: dict, seed: int) -> None:
    planted, found, _ = runs[seed]
    assert planted <= found, sorted(planted - found)


@pytest.mark.parametrize("seed", SEEDS)
def test_no_false_alerts_and_no_decoys_flagged(runs: dict, seed: int) -> None:
    planted, found, decoys = runs[seed]
    assert found - planted == set()
    assert found & decoys == set()


def test_detector_thresholds_come_from_config() -> None:
    d = CFG.detectors
    assert (d.duplicate_window_days, d.duplicate_amount_tolerance, d.price_increase_threshold,
            d.unused_min_months, d.recurring_min_months) == (7, 0.02, 0.10, 3, 3)
