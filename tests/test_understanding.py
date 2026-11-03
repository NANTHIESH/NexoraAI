"""Language understanding: parsing the model's JSON, the subscription inventory lookup, follow-up
suggestions, and the whole flow through the API with a scripted model."""

from __future__ import annotations

import copy
import json

import pytest
from fastapi.testclient import TestClient
from scripts.generate_data import generate
from scripts.load_db import load_db

from nexora.agents.lookups import lookup_kind
from nexora.agents.memory import Turn
from nexora.agents.orchestrator import Orchestrator
from nexora.agents.suggestions import follow_ups, starters
from nexora.agents.understanding import UNDERSTAND_PROMPT, parse_understanding, understand
from nexora.api.main import create_app
from nexora.config import Config, load_config
from nexora.db.repositories import frames_from_csv
from nexora.llm.ollama_client import OllamaUnavailableError
from nexora.rag.generator import Answer

PASSWORD = load_config().auth.demo_password


# ------------------------------------------------------------------ parsing the model's answer
def test_parse_valid_intent_and_clean_question() -> None:
    out = parse_understanding('{"intent": "subscription", "question": "How many subscriptions do we have?"}',
                              "how many subs do we hv")
    assert out is not None and out.intent == "subscription" and out.question == "How many subscriptions do we have?"


@pytest.mark.parametrize("text", ["", "not json", "[]", '{"intent": "dance"}', '{"question": "x"}'])
def test_parse_rejects_unusable_answers(text: str) -> None:
    assert parse_understanding(text, "hello there") is None


def test_parse_keeps_the_original_when_the_rewrite_is_empty_or_runaway() -> None:
    empty = parse_understanding('{"intent": "report", "question": ""}', "monthly report pls")
    assert empty.question == "monthly report pls"
    runaway = json.dumps({"intent": "report", "question": "x" * 500})
    assert parse_understanding(runaway, "report").question == "report"


def test_clarify_needs_wording_and_two_options() -> None:
    good = {"intent": "clarify", "question": "How are we doing?", "clarification": "Do you mean money or tasks?",
            "options": ["Give me the monthly report", "How many tasks are blocked?", "extra", "dropped"]}
    out = parse_understanding(json.dumps(good), "how are we doing?")
    assert out.intent == "clarify" and out.clarification and len(out.options) == 3
    assert parse_understanding(json.dumps({**good, "options": ["only one"]}), "q") is None
    assert parse_understanding(json.dumps({**good, "clarification": ""}), "q") is None


class _LLM:
    def __init__(self, reply: str | Exception) -> None:
        self.reply, self.prompt = reply, ""

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        self.prompt = user
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def test_understand_tells_the_model_who_is_asking_and_what_was_said_before() -> None:
    llm = _LLM('{"intent": "subscription", "question": "What does Slack cost?"}')
    out = understand("and slack?", [Turn("What does Datadog cost?", "120 EUR")], "Ruth, CEO (role: owner)", llm)
    assert out.question == "What does Slack cost?"
    assert "Ruth, CEO (role: owner)" in llm.prompt and "What does Datadog cost?" in llm.prompt


def test_understand_returns_none_when_the_model_fails() -> None:
    assert understand("hi", [], "x", _LLM(OllamaUnavailableError("http://x"))) is None
    assert "Output JSON only" in UNDERSTAND_PROMPT


# ------------------------------------------------------------------ "how many subscriptions do we have?"
@pytest.fixture(scope="module")
def frames(tmp_path_factory: pytest.TempPathFactory):
    folder = tmp_path_factory.mktemp("und")
    generate(42, folder)
    return frames_from_csv(folder)


@pytest.mark.parametrize("question", [
    "How many subscriptions do we have?", "how many tools do we pay for", "What tools do we use?",
    "List our subscriptions", "How many software licenses are there?"])
def test_inventory_questions_are_counted_not_analysed(frames, question: str) -> None:
    assert lookup_kind(question, frames) == "inventory"


@pytest.mark.parametrize("question", [
    "Which subscriptions can we cancel?", "Which tools are unused?", "Any duplicate charges?",
    "What does Datadog cost?", "How much did we spend on GitHub last month?"])
def test_other_subscription_questions_keep_their_own_answers(frames, question: str) -> None:
    assert lookup_kind(question, frames) != "inventory"


# ------------------------------------------------------------------ follow-up suggestions
def test_suggestions_fit_the_role_and_never_repeat_the_question() -> None:
    tasks = follow_ups("employee", "Which tasks are overdue?", intent="report", kind="tasks", answered=True)
    assert tasks == ["How many tasks are blocked?"]
    finance = follow_ups("owner", "How many subscriptions do we have?", intent="subscription", kind="inventory",
                         answered=True)
    assert "Which subscriptions can we cancel?" in finance and len(finance) <= 3
    for text in follow_ups("employee", "Give me the monthly report", intent="report", kind=None, answered=True):
        assert text in ("Which tasks are overdue?", "How many tasks are blocked?")  # nothing they cannot ask


def test_unanswered_questions_get_starters_to_click() -> None:
    assert follow_ups("employee", "???", intent="knowledge", kind=None, answered=False) == starters("employee")[:3]
    assert follow_ups("owner", "hi", intent="smalltalk", kind=None, answered=True) == starters("owner")[:3]


# ------------------------------------------------------------------ the whole flow with a scripted model
SCRIPT = {
    "how many subs do we hv": {"intent": "subscription", "question": "How many subscriptions do we have?"},
    "how are we doing?": {"intent": "clarify", "question": "How are we doing?",
                          "clarification": "Do you mean money, tasks or software costs?",
                          "options": ["Give me the monthly report", "How many tasks are blocked?"]},
    "how many tasks are blocked?": {"intent": "clarify", "question": "x", "clarification": "Which tasks?",
                                    "options": ["a", "b"]},
}


class ScriptedModel:
    """Understands exactly the messages in SCRIPT; every other model call fails (and falls back)."""

    model = "scripted"

    def is_ready(self) -> bool:
        return True

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        if system.startswith("You are the language-understanding step"):
            message = user.rsplit("Message: ", 1)[1].strip().lower()
            if message in SCRIPT:
                return json.dumps({"clarification": None, "options": [], **SCRIPT[message]})
        raise OllamaUnavailableError("http://scripted")


class _Assistant:
    def ask(self, question: str, include_superseded: bool | None = None) -> Answer:
        return Answer(f"Fake answer to: {question}", False, "llm")


@pytest.fixture(scope="module")
def client(tmp_path_factory: pytest.TempPathFactory) -> TestClient:
    base = tmp_path_factory.mktemp("understand_api")
    generate(42, base / "gen")
    cfg = Config(copy.deepcopy(dict(load_config())))
    cfg["paths"].update(generated=str(base / "gen"), sqlite=str(base / "u.sqlite"))
    cfg["auth"]["bcrypt_rounds"] = 4
    load_db(cfg)
    return TestClient(create_app(cfg, Orchestrator(cfg, _Assistant(), ScriptedModel())))


def ask(client: TestClient, employee: str, question: str, session: str = "u1") -> dict:
    token = client.post("/login", json={"employee_id": employee, "password": PASSWORD}).json()["token"]
    return client.post("/chat", json={"question": question, "session_id": session},
                       headers={"Authorization": f"Bearer {token}"}).json()


def test_informal_wording_is_understood_and_counted_from_the_data(client: TestClient) -> None:
    body = ask(client, "E001", "how many subs do we hv")
    assert body["intent"] == "subscription" and "software subscriptions" in body["text"]
    assert body["report"]["count"] == 18
    assert body["rewritten_question"] == "How many subscriptions do we have?"  # shown to the user
    assert body["suggestions"] and "How many subscriptions do we have?" not in body["suggestions"]


def test_vague_message_gets_a_clarifying_question_with_options_and_it_is_saved(client: TestClient) -> None:
    body = ask(client, "E001", "how are we doing?", session="vague")
    assert body["intent"] == "clarify" and body["text"] == "Do you mean money, tasks or software costs?"
    assert body["suggestions"] == ["Give me the monthly report", "How many tasks are blocked?"]
    token = client.post("/login", json={"employee_id": "E001", "password": PASSWORD}).json()["token"]
    saved = client.get("/chats/vague", headers={"Authorization": f"Bearer {token}"}).json()
    assert saved[-1]["suggestions"] == body["suggestions"]


def test_the_rules_win_when_they_already_know_what_is_meant(client: TestClient) -> None:
    body = ask(client, "E001", "How many tasks are blocked?", session="rules")  # model says "clarify"
    assert body["intent"] == "report" and "tasks are blocked" in body["text"]


def test_employees_only_get_suggestions_they_may_ask(client: TestClient) -> None:
    body = ask(client, "E005", "Which tasks are overdue?", session="emp")
    assert set(body["suggestions"]) <= {"How many tasks are blocked?", "Which tasks are overdue?"}


def test_greeting_is_personal(client: TestClient) -> None:
    body = ask(client, "E001", "hello", session="hi")
    assert body["text"].startswith("Hello Ruth!") and body["suggestions"]
    assert ask(client, "E001", "thanks!", session="hi")["text"].startswith("You are welcome, Ruth!")


def test_inventory_is_blocked_for_roles_without_subscription_access(client: TestClient) -> None:
    body = ask(client, "E005", "how many subscriptions do we have?", session="emp2")
    assert body["denied"] and "subscription" in body["text"]

