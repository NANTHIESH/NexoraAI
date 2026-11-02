"""Spending questions ("How much did we spend on GitHub last month?") are computed, not summarised."""

from __future__ import annotations

import copy
import csv
import json
from pathlib import Path

import pytest
from evaluation.eval_rules import load_questions
from fastapi.testclient import TestClient
from scripts.generate_data import generate
from scripts.load_db import load_db

from nexora.agents.orchestrator import Orchestrator
from nexora.agents.spending import answer_spending, is_spending_question, parse_months
from nexora.api.main import create_app
from nexora.config import Config, load_config
from nexora.db.repositories import frames_from_csv
from nexora.llm.ollama_client import OllamaUnavailableError
from nexora.rag.generator import Answer

PASSWORD = load_config().auth.demo_password
LAST = "2026-10"


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("spend")
    generate(42, folder)
    return folder


@pytest.fixture(scope="module")
def frames(data_dir: Path):
    return frames_from_csv(data_dir)


def rows(data_dir: Path, name: str) -> list[dict[str, str]]:
    with (data_dir / name).open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def spent(data_dir: Path, months: set[str], who: set[str] | None = None, category: str | None = None) -> float:
    """Independent calculation straight from the CSV (money out, absolute)."""
    total = 0.0
    for r in rows(data_dir, "transactions.csv"):
        amount = float(r["amount"])
        if amount < 0 and r["date"][:7] in months and (who is None or r["counterparty"] in who) \
                and (category is None or r["category"] == category):
            total -= amount
    return round(total, 2)


# ------------------------------------------------------------------ recognising the question
@pytest.mark.parametrize("question", [
    "can you tell me how much we have spent on github last month ?",
    "how much we spent on subscription alone?", "How much did we spend on travel in March?",
    "What was spent last month?", "How much do we pay for Slack?", "What did we spend on AWS this year?",
    "How much is Docker costing us?", "What are our costs for rent?"])
def test_spending_questions_are_recognised(question: str) -> None:
    assert is_spending_question(question)


@pytest.mark.parametrize("question", [
    "How much can I spend on training?", "How much can we spend without approval?",
    "How much paid time off do permanent staff get each year?", "How much paid leave do new parents get?",
    "How much do I get paid?", "How many vacation days do I get?", "Who must approve an expense above 100 EUR?",
    "Give me the monthly report", "Which subscriptions can we cancel?", "hello"])
def test_policy_personal_and_other_questions_are_not_spending_questions(question: str) -> None:
    assert not is_spending_question(question)


def test_no_evaluation_or_development_question_is_taken_for_a_spending_question() -> None:
    dev = json.loads((Path(__file__).parent / "fixtures" / "dev_questions.json").read_text(encoding="utf-8"))
    questions = ([q["question"] for q in load_questions()] + [d["question"] for d in dev["answerable"]]
                 + dev["unanswerable"])
    assert [q for q in questions if is_spending_question(q)] == []


# ------------------------------------------------------------------ the numbers
def test_github_last_month_matches_an_independent_calculation(data_dir: Path, frames) -> None:
    answer = answer_spending(frames, load_config(), "can you tell me how much we have spent on github last month ?")
    expected = spent(data_dir, {LAST}, {"GitHub"})
    assert answer.data["total_eur"] == expected and expected > 0
    assert f"{expected:,.2f} EUR on GitHub in {LAST}" in answer.text


def test_subscriptions_without_a_period_give_last_month_and_the_year(data_dir: Path, frames) -> None:
    answer = answer_spending(frames, load_config(), "how much we spent on subscription alone?")
    listed = {r["tool"] for r in rows(data_dir, "subscriptions.csv")}
    months = {r["date"][:7] for r in rows(data_dir, "transactions.csv")}
    assert answer.data["total_eur"] == spent(data_dir, {LAST}, listed)
    assert answer.data["year_total_eur"] == spent(data_dir, months, listed)
    assert f"{answer.data['total_eur']:,.2f} EUR" in answer.text
    assert f"{answer.data['year_total_eur']:,.2f} EUR" in answer.text
    assert "Not included" in answer.text and "Canva" in answer.text  # unlisted software charges are mentioned


def test_category_and_month_name_without_year(data_dir: Path, frames) -> None:
    answer = answer_spending(frames, load_config(), "How much did we spend on travel in March?")
    assert answer.data["period_months"] == ["2026-03"]
    assert answer.data["total_eur"] == spent(data_dir, {"2026-03"}, category="Travel")


def test_whole_year_for_one_vendor_lists_each_month(data_dir: Path, frames) -> None:
    answer = answer_spending(frames, load_config(), "How much did we spend on AWS this year?")
    all_months = {r["date"][:7] for r in rows(data_dir, "transactions.csv")}
    assert answer.data["total_eur"] == spent(data_dir, all_months, {"AWS"})
    assert len(answer.data["by_month"]) == 12


def test_duplicate_charge_shows_both_charges(data_dir: Path, frames) -> None:
    truth = json.loads((data_dir / "ground_truth.json").read_text(encoding="utf-8"))
    dup = next(e for e in truth["errors"] if e["type"] == "duplicate_charge")
    answer = answer_spending(frames, load_config(), f"How much did we spend on {dup['tool']} in {dup['month']}?")
    assert len(answer.data["charges"]) == 2 and "2 charges" in answer.text


def test_all_expenses_when_nothing_is_named(data_dir: Path, frames) -> None:
    answer = answer_spending(frames, load_config(), "How much did we spend last month?")
    assert answer.data["total_eur"] == spent(data_dir, {LAST}) and "all expenses" in answer.text


@pytest.mark.parametrize("question", ["How much did we spend on pizza?",
                                      "How much did we spend on the moon last month?"])
def test_unknown_things_get_a_helpful_message_not_a_wrong_number(frames, question: str) -> None:
    answer = answer_spending(frames, load_config(), question)
    assert "error" in answer.data and "Try:" in answer.text


def test_month_outside_the_data_says_so(frames) -> None:
    answer = answer_spending(frames, load_config(), "How much did we spend on GitHub in 2031-05?")
    assert "no data" in answer.text.lower() and "2025-11 to 2026-10" in answer.text
    assert "no data" in answer_spending(frames, load_config(), "How much did we spend in March 2099?").text.lower()


@pytest.mark.parametrize(("question", "months", "label"), [
    ("last month", ["2026-10"], "2026-10"), ("in october", ["2026-10"], "2026-10"),
    ("in Nov 2025", ["2025-11"], "2025-11"), ("over the last quarter", ["2026-08", "2026-09", "2026-10"], None),
    ("in 2026-03", ["2026-03"], "2026-03")])
def test_period_parsing(question: str, months: list[str], label: str | None) -> None:
    available = [f"2025-{m:02d}" for m in (11, 12)] + [f"2026-{m:02d}" for m in range(1, 11)]
    parsed = parse_months(question, available)
    assert parsed is not None and parsed[0] == months
    assert label is None or parsed[1] == label
    assert parse_months("how much did we spend", available) is None


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
    cfg["paths"].update(generated=str(data_dir), sqlite=str(tmp_path_factory.mktemp("db") / "s.sqlite"))
    cfg["auth"]["bcrypt_rounds"] = 4
    load_db(cfg)
    return TestClient(create_app(cfg, Orchestrator(cfg, NoDocs(), OfflineLLM())))


def ask(client: TestClient, employee_id: str, question: str) -> dict:
    token = client.post("/login", json={"employee_id": employee_id, "password": PASSWORD}).json()["token"]
    return client.post("/chat", json={"question": question, "session_id": employee_id + question[:8]},
                       headers={"Authorization": f"Bearer {token}"}).json()


def test_chat_answers_the_users_github_question_with_the_computed_number(client: TestClient, data_dir: Path) -> None:
    body = ask(client, "E002", "can you tell me how much we have spent on github last month ?")
    expected = spent(data_dir, {LAST}, {"GitHub"})
    assert body["intent"] == "report" and body["method"].endswith("+rule")
    assert f"{expected:,.2f} EUR" in body["text"] and "Income" not in body["text"]  # not the generic monthly report


def test_chat_answers_the_subscription_total_question_not_the_savings_list(client: TestClient) -> None:
    body = ask(client, "E001", "how much we spent on subscription alone?")
    assert body["intent"] == "report" and "We spent" in body["text"] and "Estimated savings" not in body["text"]


def test_spending_lookup_is_denied_to_pm_and_employees(client: TestClient) -> None:
    for employee in ("E003", "E005"):
        body = ask(client, employee, "How much did we spend on GitHub last month?")
        assert body["denied"] and "does not have access" in body["text"] and body["report"] is None
