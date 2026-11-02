"""Computed answers for income, tasks, rankings, headcount and tool facts, and how chat routes to them."""

from __future__ import annotations

import copy
import csv
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scripts.generate_data import generate
from scripts.load_db import load_db

from nexora.agents.lookups import (
    answer_headcount,
    answer_income,
    answer_ranking,
    answer_tasks,
    answer_tool_facts,
    lookup_kind,
    topic_of,
)
from nexora.agents.orchestrator import Orchestrator
from nexora.agents.report import compute_report
from nexora.api.main import create_app
from nexora.config import Config, load_config
from nexora.db.repositories import frames_from_csv
from nexora.llm.ollama_client import OllamaUnavailableError
from nexora.rag.generator import Answer

PASSWORD = load_config().auth.demo_password
CFG = load_config()


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("lookups")
    generate(42, folder)
    return folder


@pytest.fixture(scope="module")
def frames(data_dir: Path):
    return frames_from_csv(data_dir)


def rows(data_dir: Path, name: str) -> list[dict[str, str]]:
    with (data_dir / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------------ which lookup
@pytest.mark.parametrize(("question", "kind"), [
    ("How much did we spend on GitHub in January?", "spending"),  # a tool name must not turn this into tool facts
    ("How many people use Slack?", "tool_facts"),  # "how many people" must not mean headcount
    ("What does the Datadog subscription cost?", "tool_facts"),
    ("How many employees do we have?", "headcount"),
    ("What is our biggest expense?", "ranking"),
    ("Which vendor is the largest cost?", "ranking"),
    ("How many tasks are blocked?", "tasks"),
    ("Which tasks are overdue?", "tasks"),
    ("What was our income in March?", "income"),
    ("What is our net profit this year?", "income"),
    ("Give me the monthly report", None),
    ("Which subscriptions can we cancel?", None),
    ("How many vacation days do I get?", None)])
def test_lookup_kind(frames, question: str, kind: str | None) -> None:
    assert lookup_kind(question, frames) == kind


@pytest.mark.parametrize(("question", "topic"), [
    ("Which tools are unused?", "unused_subscription"), ("Any duplicate charges?", "duplicate_charge"),
    ("Did any price increase?", "price_increase"), ("Which tools overlap?", "overlap"),
    ("Any charges from tools not on the list?", "unlisted_charge"), ("What can we cancel?", None)])
def test_topic_detection(question: str, topic: str | None) -> None:
    assert topic_of(question) == topic


# ------------------------------------------------------------------ the numbers
def test_income_for_a_month_matches_an_independent_calculation(data_dir: Path, frames) -> None:
    answer = answer_income(frames, "What was our income in March?")
    txns = [r for r in rows(data_dir, "transactions.csv") if r["date"][:7] == "2026-03"]
    income = round(sum(float(r["amount"]) for r in txns if float(r["amount"]) > 0), 2)
    expenses = round(-sum(float(r["amount"]) for r in txns if float(r["amount"]) < 0), 2)
    assert (answer.data["income"], answer.data["expenses"]) == (income, expenses)
    assert answer.data["net"] == round(income - expenses, 2) and f"{income:,.2f} EUR" in answer.text
    assert answer.data["period_months"] == ["2026-03"]


def test_income_for_the_year_agrees_with_the_yearly_report(frames) -> None:
    answer = answer_income(frames, "What is our net profit this year?")
    report = compute_report(frames, "yearly")
    assert (answer.data["income"], answer.data["expenses"], answer.data["net"]) == (
        report["income"], report["expenses"], report["net"])
    assert len(answer.data["by_month"]) == 12


def test_income_outside_the_data_says_so(frames) -> None:
    assert "no data" in answer_income(frames, "What was our income in March 2099?").text.lower()


def test_biggest_expense_category_and_vendor(data_dir: Path, frames) -> None:
    by_category: dict[str, float] = {}
    by_vendor: dict[str, float] = {}
    for r in rows(data_dir, "transactions.csv"):
        if float(r["amount"]) < 0:
            by_category[r["category"]] = by_category.get(r["category"], 0.0) - float(r["amount"])
            by_vendor[r["counterparty"]] = by_vendor.get(r["counterparty"], 0.0) - float(r["amount"])
    answer = answer_ranking(frames, "What is our biggest expense?")
    assert next(iter(answer.data["top"])) == max(by_category, key=by_category.get)
    assert answer.data["top"]["Payroll"] == round(by_category["Payroll"], 2)
    vendor = answer_ranking(frames, "Which vendor is the largest cost?")
    assert next(iter(vendor.data["top"])) == max(by_vendor, key=by_vendor.get)


def test_headcount_matches_the_employee_file(data_dir: Path, frames) -> None:
    answer = answer_headcount(frames)
    assert answer.data["employees"] == len(rows(data_dir, "employees.csv")) == 15
    assert answer.data["by_job_title"]["Developer"] == 6


def test_task_counts_match_the_csv(data_dir: Path, frames) -> None:
    tasks = rows(data_dir, "tasks.csv")
    blocked = answer_tasks(frames, "How many tasks are blocked?")
    done = answer_tasks(frames, "How many tasks are done?")
    assert blocked.data["count"] == sum(t["status"] == "blocked" for t in tasks)
    assert done.data["count"] == sum(t["status"] == "done" for t in tasks)
    assert answer_tasks(frames, "How many tasks are in progress?").data["count"] == sum(
        t["status"] == "in_progress" for t in tasks)


def test_overdue_count_agrees_with_the_monthly_report(frames) -> None:
    overdue = answer_tasks(frames, "Which tasks are overdue?").data["count"]
    assert overdue == compute_report(frames, "monthly")["tasks"]["overdue"]


def test_task_list_is_most_urgent_first_and_limited(frames) -> None:
    answer = answer_tasks(frames, "Which tasks are overdue?")
    listed = [line for line in answer.text.splitlines() if line.startswith("- NX-")]
    assert len(listed) == 10 and "(+" in answer.text
    order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    priorities = [order[line.split("[")[1].split("]")[0]] for line in listed]
    assert priorities == sorted(priorities)


def test_tasks_can_be_limited_to_one_person_or_one_project(data_dir: Path, frames) -> None:
    tasks = rows(data_dir, "tasks.csv")
    mine = answer_tasks(frames, "How many tasks are blocked?", employee_id="E005")
    assert mine.data["count"] == sum(t["status"] == "blocked" and t["owner"] == "E005" for t in tasks)
    assert mine.data["scope"] == "your tasks"
    project = answer_tasks(frames, "How many tasks are blocked in LogiDash?")
    assert project.data["count"] == sum(t["status"] == "blocked" and t["project"] == "LogiDash" for t in tasks)


def test_tool_facts_come_from_the_data(data_dir: Path, frames) -> None:
    flags = []
    answer = answer_tool_facts(frames, CFG, "How many people use Slack?", flags)
    sub = next(r for r in rows(data_dir, "subscriptions.csv") if r["tool"] == "Slack")
    usage = [r for r in rows(data_dir, "usage_log.csv") if r["tool"] == "Slack"][-1]
    assert answer.data["seats"] == int(sub["seats"]) and answer.data["active_users"] == int(usage["active_users"])
    assert f"{usage['active_users']} of {sub['seats']} seats active in {usage['month']}" in answer.text
    assert sub["owner"] in answer.text


# ------------------------------------------------------------------ through the API
class OfflineLLM:
    model = "fake"

    def is_ready(self) -> bool:
        return False

    def chat(self, *_: object, **__: object) -> str:
        raise OllamaUnavailableError("http://localhost:1")


class NoDocs:
    def ask(self, question: str, include_superseded: bool | None = None) -> Answer:
        return Answer("document answer", False, "extractive")


@pytest.fixture(scope="module")
def client(data_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    cfg = Config(copy.deepcopy(dict(load_config())))
    cfg["paths"].update(generated=str(data_dir), sqlite=str(tmp_path_factory.mktemp("db") / "l.sqlite"))
    cfg["auth"]["bcrypt_rounds"] = 4
    load_db(cfg)
    return TestClient(create_app(cfg, Orchestrator(cfg, NoDocs(), OfflineLLM())))


def ask(client: TestClient, employee_id: str, question: str) -> dict:
    token = client.post("/login", json={"employee_id": employee_id, "password": PASSWORD}).json()["token"]
    return client.post("/chat", json={"question": question, "session_id": employee_id + question[:10]},
                       headers={"Authorization": f"Bearer {token}"}).json()


@pytest.mark.parametrize(("question", "intent", "expect"), [
    ("How many tasks are blocked?", "report", "tasks are blocked"),
    ("How many employees do we have?", "report", "15 employees"),
    ("What was our income in March?", "report", "In 2026-03: income"),
    ("What is our biggest expense?", "report", "Largest expense categories"),
    ("How much did we spend on GitHub in January?", "report", "on GitHub in 2026-01"),
    ("How many people use Slack?", "subscription", "seats active"),
    ("What does the Datadog subscription cost?", "subscription", "Datadog (Pro"),
    ("Which tools are unused?", "subscription", "unused subscriptions found"),
    ("Any duplicate charges?", "subscription", "duplicate charges found"),
])
def test_chat_routes_data_questions_to_the_right_computed_answer(
        client: TestClient, question: str, intent: str, expect: str) -> None:
    body = client.post("/chat", json={"question": question, "session_id": "s" + question[:12]},
                       headers={"Authorization": "Bearer " + client.post(
                           "/login", json={"employee_id": "E002", "password": PASSWORD}).json()["token"]}).json()
    assert body["intent"] == intent and expect in body["text"], body["text"][:200]


def test_unrelated_data_question_gets_an_honest_message_not_a_report(client: TestClient) -> None:
    body = ask(client, "E002", "What is our cash flow situation?")
    assert "could not match" in body["text"] and "Income" not in body["text"] and body["report"] is None


def test_topic_filter_lists_only_that_problem_type(client: TestClient) -> None:
    body = ask(client, "E002", "Which tools are unused?")
    assert body["text"].count("Cancel ") == 4 and "refund" not in body["text"].split("\n\n")[1]


def test_roles_are_respected_by_every_lookup(client: TestClient) -> None:
    for question in ("What was our income in March?", "What is our biggest expense?"):
        assert ask(client, "E003", question)["denied"] and ask(client, "E005", question)["denied"]
    assert ask(client, "E003", "What does the Datadog subscription cost?")["denied"]
    pm_tasks = ask(client, "E003", "How many tasks are blocked?")
    assert not pm_tasks["denied"] and pm_tasks["report"]["scope"] == "all projects"
    employee_tasks = ask(client, "E005", "How many tasks are blocked?")
    assert not employee_tasks["denied"] and employee_tasks["report"]["scope"] == "your tasks"
    assert employee_tasks["report"]["count"] <= pm_tasks["report"]["count"]


def test_generic_reports_still_work(client: TestClient) -> None:
    for question, period in (("Give me the monthly report", "monthly"), ("weekly report please", "weekly"),
                             ("yearly report", "yearly")):
        body = ask(client, "E002", question)
        assert body["report"]["period"] == period and "Net" in body["text"]
    assert ask(client, "E002", "Which subscriptions can we cancel?")["text"].startswith("I found 20 issues")


def test_the_reference_date_is_end_of_last_month(frames) -> None:
    assert date.fromisoformat(compute_report(frames, "monthly")["end"]) == date(2026, 10, 31)
