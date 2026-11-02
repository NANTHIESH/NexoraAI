"""Phase 4 tests: database loading, report numbers, subscription agent, router."""

from __future__ import annotations

import copy
import csv
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from scripts.generate_data import generate
from scripts.load_db import load_db

from nexora.agents.report import build_report, compute_report, template_summary, window
from nexora.agents.router import keyword_route, route
from nexora.agents.subscription import analyze_subscriptions
from nexora.agents.tools import parse_period
from nexora.config import Config, load_config
from nexora.db.repositories import MissingDataError, frames_from_csv, frames_from_db
from nexora.db.session import connect


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("agentdata")
    generate(42, folder)
    return folder


@pytest.fixture(scope="module")
def frames(data_dir: Path):
    return frames_from_csv(data_dir)


class FakeLLM:
    def __init__(self, reply: str | Exception) -> None:
        self.reply = reply

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


# ------------------------------------------------------------------ database
def test_load_db_matches_csv_and_is_repeatable(data_dir: Path, tmp_path: Path) -> None:
    cfg = Config(copy.deepcopy(dict(load_config())))
    cfg["paths"].update(generated=str(data_dir), sqlite=str(tmp_path / "t.sqlite"))
    first = load_db(cfg)
    assert load_db(cfg) == first  # reloading replaces rows, no duplicates
    assert first == {"employees": 15, "transactions": 1015, "subscriptions": 18,
                     "usage_log": 199, "plan_changes": 2, "tasks": 250}
    conn = connect(tmp_path / "t.sqlite")
    from_db, from_csv = frames_from_db(conn), frames_from_csv(data_dir)
    pd.testing.assert_frame_equal(from_db.transactions.reset_index(drop=True),
                                  from_csv.transactions.reset_index(drop=True), check_dtype=False)


def test_load_db_names_the_make_target_when_data_is_missing(tmp_path: Path) -> None:
    cfg = Config(copy.deepcopy(dict(load_config())))
    cfg["paths"].update(generated=str(tmp_path / "none"), sqlite=str(tmp_path / "t.sqlite"))
    with pytest.raises(FileNotFoundError, match="make data"):
        load_db(cfg)


def test_empty_database_gives_clear_error(tmp_path: Path) -> None:
    with pytest.raises(MissingDataError, match="make load"):
        frames_from_db(connect(tmp_path / "empty.sqlite"))


# ------------------------------------------------------------------ report numbers
def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


@pytest.mark.parametrize("period", ["weekly", "monthly", "yearly"])
def test_report_numbers_match_an_independent_calculation(data_dir: Path, frames, period: str) -> None:
    data = compute_report(frames, period)
    start, end = date.fromisoformat(data["start"]), date.fromisoformat(data["end"])
    rows = [r for r in read_csv(data_dir / "transactions.csv")
            if start <= date.fromisoformat(r["date"]) <= end]
    income = sum(float(r["amount"]) for r in rows if float(r["amount"]) > 0)
    expenses = -sum(float(r["amount"]) for r in rows if float(r["amount"]) < 0)
    by_cat: dict[str, float] = {}
    for r in rows:
        if float(r["amount"]) < 0:
            by_cat[r["category"]] = by_cat.get(r["category"], 0.0) - float(r["amount"])
    assert data["income"] == pytest.approx(income, abs=0.01)
    assert data["expenses"] == pytest.approx(expenses, abs=0.01)
    assert data["net"] == pytest.approx(income - expenses, abs=0.01)
    assert data["expenses_by_category"] == pytest.approx({k: round(v, 2) for k, v in by_cat.items()})

    tasks = read_csv(data_dir / "tasks.csv")
    done = [t for t in tasks if t["completed"] and start <= date.fromisoformat(t["completed"]) <= end]
    existing = [t for t in tasks if date.fromisoformat(t["created"]) <= end]
    assert data["tasks"]["done"] == len(done)
    assert data["tasks"]["blocked"] == sum(t["status"] == "blocked" for t in existing)
    assert data["tasks"]["overdue"] == sum(
        t["status"] != "done" and date.fromisoformat(t["due"]) < end for t in existing)


def test_top_cost_increases_are_the_three_largest_category_rises(frames) -> None:
    data = compute_report(frames, "monthly")
    rises = data["top_cost_increases"]
    assert 0 < len(rises) <= 3
    assert [r["increase"] for r in rises] == sorted((r["increase"] for r in rises), reverse=True)
    for r in rises:
        assert r["increase"] == pytest.approx(r["current"] - r["previous"], abs=0.01)
    start, _, prev_start, prev_end = window("monthly", date.fromisoformat(data["end"]))
    assert (prev_start, prev_end) == (date(2026, 9, 1), date(2026, 9, 30)) and start == date(2026, 10, 1)


def test_blocked_task_list_is_sorted_by_priority(frames) -> None:
    listed = compute_report(frames, "yearly")["blocked_tasks"]
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    assert listed and [order[t["priority"]] for t in listed] == sorted(order[t["priority"]] for t in listed)


def test_invalid_period_is_rejected(frames) -> None:
    with pytest.raises(ValueError, match="weekly"):
        compute_report(frames, "daily")


# ------------------------------------------------------------------ report summary (LLM never calculates)
def test_summary_uses_template_when_model_is_unavailable(frames) -> None:
    report = build_report(frames, "monthly", llm=FakeLLM(ConnectionError("down")))
    assert report.summary_source == "template" and report.summary == template_summary(report.data)


def test_summary_with_invented_numbers_is_rejected(frames) -> None:
    report = build_report(frames, "monthly", llm=FakeLLM("Net result was 9,999,999 EUR."))
    assert report.summary_source == "template"


def test_summary_using_only_given_numbers_is_accepted(frames) -> None:
    data = compute_report(frames, "monthly")
    text = f"Income was {data['income']:,.0f} EUR and expenses were {data['expenses']:,.0f} EUR."
    report = build_report(frames, "monthly", llm=FakeLLM(text))
    assert report.summary_source == "llm" and report.summary == text


# ------------------------------------------------------------------ subscription agent
def test_subscription_agent_recommendations_and_savings(frames) -> None:
    analysis = analyze_subscriptions(frames)
    assert len(analysis.flags) == 20 and len(analysis.recommendations) == 20
    assert all(r.requires_confirmation for r in analysis.recommendations)
    cancel = [r.text for r in analysis.recommendations if r.tool == "Datadog"]
    assert cancel == ["Cancel Datadog: 0 active users for 6 months, saves 120 EUR/month"]
    assert analysis.yearly_savings == pytest.approx(analysis.monthly_savings * 12, abs=0.01)
    assert analysis.one_off_recovery == pytest.approx(
        sum(f.one_off_recovery for f in analysis.flags), abs=0.01)


def test_price_rise_is_not_double_counted_for_a_tool_that_is_cancelled(frames) -> None:
    analysis = analyze_subscriptions(frames)
    types = {f.type for f in analysis.flags if f.tool == "Postman"}
    assert types == {"unused_subscription", "price_increase"}
    unused = next(f for f in analysis.flags if f.tool == "Postman" and f.type == "unused_subscription")
    assert analysis.savings_by_tool["Postman"] == unused.monthly_saving


# ------------------------------------------------------------------ router
@pytest.mark.parametrize(("question", "intent"), [
    ("How many vacation days do I get?", "knowledge"),
    ("Give me the monthly report", "report"),
    ("Show tasks that are blocked", "report"),
    ("What was spent last month?", "report"),
    ("Which subscriptions can we cancel?", "subscription"),
    ("Do we pay for two chat tools?", "subscription"),
    ("hello", "smalltalk"),
    ("Thanks!", "smalltalk"),
])
def test_keyword_router(question: str, intent: str) -> None:
    assert keyword_route(question) == intent


def test_router_uses_the_model_when_it_answers_validly(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="nexora.router"):
        result = route("anything", FakeLLM('{"intent": "report"}'))
    assert (result.intent, result.method) == ("report", "llm")
    assert "intent=report method=llm" in caplog.text


@pytest.mark.parametrize("reply", ["not json", '{"intent": "banana"}', "[]", ConnectionError("down")])
def test_router_falls_back_to_keywords(reply: str | Exception) -> None:
    result = route("Which subscriptions can we cancel?", FakeLLM(reply))
    assert (result.intent, result.method) == ("subscription", "keywords")


def test_router_without_model_uses_keywords() -> None:
    assert route("monthly report please").method == "keywords"


@pytest.mark.parametrize(("question", "period"), [
    ("weekly report", "weekly"), ("how did we do this year", "yearly"),
    ("the monthly numbers", "monthly"), ("income report", "monthly")])
def test_parse_period(question: str, period: str) -> None:
    assert parse_period(question) == period
