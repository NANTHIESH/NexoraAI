"""Phase 1 tests: generator determinism, sizes, planted errors and data consistency."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from scripts.generate_data import generate

from nexora.config import load_config

SEEDS = (42, 7, 123)
FILES = ("employees.csv", "subscriptions.csv", "usage_log.csv", "transactions.csv",
         "tasks.csv", "plan_changes.csv", "ground_truth.json")


def read(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.fixture(scope="module")
def datasets(tmp_path_factory: pytest.TempPathFactory) -> dict[int, Path]:
    out = {}
    for seed in SEEDS:
        out[seed] = tmp_path_factory.mktemp(f"gen{seed}")
        generate(seed, out[seed])
    return out


def test_same_seed_is_byte_identical(datasets: dict[int, Path], tmp_path: Path) -> None:
    generate(42, tmp_path)
    for name in FILES:
        assert (tmp_path / name).read_bytes() == (datasets[42] / name).read_bytes(), name


def test_different_seeds_differ(datasets: dict[int, Path]) -> None:
    assert (datasets[42] / "transactions.csv").read_bytes() != (
        datasets[7] / "transactions.csv").read_bytes()


def test_default_seed_comes_from_config() -> None:
    assert load_config().seed == 42


@pytest.mark.parametrize("seed", SEEDS)
def test_row_counts(datasets: dict[int, Path], seed: int) -> None:
    d = datasets[seed]
    employees = read(d / "employees.csv")
    assert len(employees) == 15
    assert Counter(e["role"] for e in employees) == {
        "owner": 1, "ops_manager": 1, "pm": 1, "employee": 12}
    assert len(read(d / "subscriptions.csv")) == 18
    assert 900 <= len(read(d / "transactions.csv")) <= 1100
    assert len(read(d / "tasks.csv")) == 250
    months = {r["month"] for r in read(d / "usage_log.csv")}
    assert len(months) == 12


@pytest.mark.parametrize("seed", SEEDS)
def test_exactly_20_planted_errors_by_type(datasets: dict[int, Path], seed: int) -> None:
    truth = json.loads((datasets[seed] / "ground_truth.json").read_text(encoding="utf-8"))
    errors = truth["errors"]
    assert len(errors) == 20
    assert Counter(e["type"] for e in errors) == {
        "duplicate_charge": 5, "price_increase": 5, "unused_subscription": 4,
        "unlisted_charge": 3, "overlap": 3}
    keys = [e["expected_detection_key"] for e in errors]
    assert len(set(keys)) == 20
    for e in errors:
        assert e["expected_detection_key"] == f"{e['type']}|{e['tool']}|{e['month']}"
    assert len(truth["decoys"]) >= 4


@pytest.mark.parametrize("seed", SEEDS)
def test_software_transactions_match_subscription_or_unlisted(
        datasets: dict[int, Path], seed: int) -> None:
    d = datasets[seed]
    tools = {s["tool"] for s in read(d / "subscriptions.csv")}
    truth = json.loads((d / "ground_truth.json").read_text(encoding="utf-8"))
    unlisted = {e["tool"] for e in truth["errors"] if e["type"] == "unlisted_charge"}
    software = [t for t in read(d / "transactions.csv") if t["category"] in ("Software", "Cloud")]
    assert software
    for txn in software:
        assert txn["counterparty"] in tools or txn["counterparty"] in unlisted, txn
    assert unlisted.isdisjoint(tools)


@pytest.mark.parametrize("seed", SEEDS)
def test_ground_truth_references_real_transactions(datasets: dict[int, Path], seed: int) -> None:
    d = datasets[seed]
    ids = {t["txn_id"] for t in read(d / "transactions.csv")}
    truth = json.loads((d / "ground_truth.json").read_text(encoding="utf-8"))
    for item in truth["errors"] + truth["decoys"]:
        assert item["txn_ids"] and set(item["txn_ids"]) <= ids


@pytest.mark.parametrize("seed", SEEDS)
def test_planted_patterns_hold(datasets: dict[int, Path], seed: int) -> None:
    d = datasets[seed]
    txns = {t["txn_id"]: t for t in read(d / "transactions.csv")}
    usage: dict[str, dict[str, int]] = {}
    for row in read(d / "usage_log.csv"):
        usage.setdefault(row["tool"], {})[row["month"]] = int(row["active_users"])
    truth = json.loads((d / "ground_truth.json").read_text(encoding="utf-8"))
    for e in truth["errors"]:
        rows = [txns[i] for i in e["txn_ids"]]
        if e["type"] == "duplicate_charge":
            a, b = sorted(rows, key=lambda r: r["date"])
            gap = (date.fromisoformat(b["date"]) - date.fromisoformat(a["date"])).days
            assert 1 <= gap <= 5 and a["date"][:7] == b["date"][:7] == e["month"]
            assert abs(float(a["amount"]) - float(b["amount"])) / abs(float(a["amount"])) < 0.02
        elif e["type"] == "unused_subscription":
            months = sorted(m for m, users in usage[e["tool"]].items()
                            if m >= e["month"])
            assert len(months) >= 3 and all(usage[e["tool"]][m] == 0 for m in months)
            assert len(rows) >= 3  # still billed
        elif e["type"] == "unlisted_charge":
            assert len(rows) >= 3
        elif e["type"] == "price_increase":
            before = [t for t in txns.values()
                      if t["counterparty"] == e["tool"] and t["date"][:7] < e["month"]]
            prev = abs(float(before[-1]["amount"]))
            rise = abs(float(rows[0]["amount"])) / prev - 1
            assert 0.15 <= rise <= 0.61
