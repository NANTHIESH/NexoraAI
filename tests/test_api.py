"""Phase 5 tests: API endpoints, authentication, role-based access control, logging."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scripts.generate_data import generate
from scripts.load_db import load_db

from nexora.agents.orchestrator import Orchestrator
from nexora.api.main import create_app
from nexora.config import Config, load_config
from nexora.db.session import connect
from nexora.llm.ollama_client import OllamaUnavailableError
from nexora.rag.generator import Answer
from nexora.security.auth import make_token
from nexora.voice.tts import VoiceUnavailableError

PASSWORD = load_config().auth.demo_password
USERS = {"owner": "E001", "ops_manager": "E002", "pm": "E003", "employee": "E005"}


class OfflineLLM:
    """Ollama stand-in that is never ready."""

    model = "fake"

    def is_ready(self) -> bool:
        return False

    def chat(self, *_: object, **__: object) -> str:
        raise OllamaUnavailableError("http://localhost:1")


class FakeAssistant:
    """Knowledge answers without an index or a model."""

    def ask(self, question: str, include_superseded: bool | None = None) -> Answer:
        return Answer(f"Fake answer to: {question}", False, "extractive", citations=["[Doc, Sec]"])


@pytest.fixture(scope="module")
def cfg(tmp_path_factory: pytest.TempPathFactory) -> Config:
    base = tmp_path_factory.mktemp("api")
    generate(42, base / "gen")
    config = Config(copy.deepcopy(dict(load_config())))
    config["paths"].update(generated=str(base / "gen"), sqlite=str(base / "api.sqlite"))
    config["auth"]["bcrypt_rounds"] = 4  # fast hashing in tests
    load_db(config)
    return config


@pytest.fixture(scope="module")
def client(cfg: Config) -> TestClient:
    return TestClient(create_app(cfg, Orchestrator(cfg, FakeAssistant(), OfflineLLM())))


def auth(client: TestClient, role: str) -> dict[str, str]:
    resp = client.post("/login", json={"employee_id": USERS[role], "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


# ------------------------------------------------------------------ public endpoints and login
def test_health_reports_extractive_mode_when_ollama_is_down(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["mode"] == "extractive" and not body["llm_ready"]
    assert "extractive" in body["notice"]


def test_user_list_for_login_selection(client: TestClient) -> None:
    users = client.get("/users").json()
    assert len(users) == 15 and {"employee_id", "name", "role", "job_title"} == set(users[0])


def test_login_returns_token_and_user(client: TestClient) -> None:
    body = client.post("/login", json={"employee_id": "E002", "password": PASSWORD}).json()
    assert body["user"]["role"] == "ops_manager" and body["token"]


def test_wrong_password_is_401_and_audited(client: TestClient, cfg: Config) -> None:
    assert client.post("/login", json={"employee_id": "E002", "password": "nope"}).status_code == 401
    rows = connect(cfg.path("sqlite")).execute(
        "SELECT user_id FROM audit_log WHERE action = 'login_failed'").fetchall()
    assert [r["user_id"] for r in rows].count("E002") >= 1


def test_passwords_are_stored_as_bcrypt_hashes(cfg: Config) -> None:
    hashes = [r[0] for r in connect(cfg.path("sqlite")).execute("SELECT password_hash FROM credentials")]
    assert len(hashes) == 15 and all(h.startswith("$2") and PASSWORD not in h for h in hashes)
    assert len(set(hashes)) == 15  # unique salt per user


@pytest.mark.parametrize(("method", "path"), [
    ("post", "/chat"), ("get", "/report"), ("get", "/subscriptions/analysis"), ("get", "/tasks"),
    ("get", "/dashboard"), ("post", "/feedback"), ("post", "/voice/tts")])
def test_protected_endpoints_require_a_token(client: TestClient, method: str, path: str) -> None:
    assert getattr(client, method)(path).status_code == 401


def test_tampered_or_expired_token_is_rejected(client: TestClient) -> None:
    good = auth(client, "owner")["Authorization"]
    assert client.get("/tasks", headers={"Authorization": good[:-3] + "abc"}).status_code == 401
    expired = make_token("E001", ttl_minutes=-1)
    assert client.get("/tasks", headers={"Authorization": f"Bearer {expired}"}).status_code == 401


# ------------------------------------------------------------------ RBAC
def test_employee_cannot_get_a_finance_report(client: TestClient) -> None:
    resp = client.get("/report?period=monthly", headers=auth(client, "employee"))
    assert resp.status_code == 403 and "finance" in resp.json()["detail"]


@pytest.mark.parametrize("role", ["employee", "pm"])
def test_only_owner_and_ops_manager_see_subscription_analysis(client: TestClient, role: str) -> None:
    assert client.get("/subscriptions/analysis", headers=auth(client, role)).status_code == 403


def test_pm_gets_project_report_without_money_but_not_finance(client: TestClient) -> None:
    headers = auth(client, "pm")
    assert client.get("/report?kind=finance", headers=headers).status_code == 403
    body = client.get("/report?kind=project&period=monthly", headers=headers).json()
    assert set(body["report"]) >= {"tasks", "blocked_tasks"}
    assert not {"income", "expenses", "net", "expenses_by_category"} & set(body["report"])


def test_employee_cannot_get_project_report_either(client: TestClient) -> None:
    assert client.get("/report?kind=project", headers=auth(client, "employee")).status_code == 403


@pytest.mark.parametrize("role", ["owner", "ops_manager"])
def test_owner_and_ops_manager_have_full_access(client: TestClient, role: str) -> None:
    headers = auth(client, role)
    report = client.get("/report?period=monthly", headers=headers).json()
    assert report["report"]["income"] > 0 and report["summary"]
    analysis = client.get("/subscriptions/analysis", headers=headers).json()
    assert len(analysis["flags"]) == 20 and analysis["monthly_savings"] > 0
    assert all(r["requires_confirmation"] for r in analysis["recommendations"])


def test_tasks_are_scoped_by_role(client: TestClient) -> None:
    mine = client.get("/tasks", headers=auth(client, "employee")).json()
    assert mine and {t["owner"] for t in mine} == {"E005"}
    assert len(client.get("/tasks", headers=auth(client, "pm")).json()) == 250
    assert len(client.get("/tasks?status=blocked", headers=auth(client, "owner")).json()) < 250


def test_dashboard_sections_follow_the_role(client: TestClient) -> None:
    emp = client.get("/dashboard", headers=auth(client, "employee")).json()
    assert emp["finance"] is None and emp["subscriptions"] is None and emp["tasks"]["scope"] == "own"
    pm = client.get("/dashboard", headers=auth(client, "pm")).json()
    assert pm["finance"] is None and pm["tasks"]["scope"] == "all"
    full = client.get("/dashboard", headers=auth(client, "ops_manager")).json()
    assert len(full["finance"]["monthly"]) == 12 and len(full["subscriptions"]["anomalies"]) == 20
    assert full["subscriptions"]["table"][0]["active_users"] >= 0


# ------------------------------------------------------------------ chat, history, audit, feedback
def test_chat_answers_documents_questions_for_every_role(client: TestClient) -> None:
    for role in USERS:
        body = client.post("/chat", json={"question": "How many vacation days do I get?"},
                           headers=auth(client, role)).json()
        assert body["intent"] == "knowledge" and body["text"].startswith("Fake answer")


def test_chat_denies_finance_questions_to_employees_and_logs_it(client: TestClient, cfg: Config) -> None:
    body = client.post("/chat", json={"question": "Give me the monthly report"},
                       headers=auth(client, "employee")).json()
    assert body["denied"] and body["report"] is None and "does not have access" in body["text"]
    row = connect(cfg.path("sqlite")).execute(
        "SELECT action, intent, role FROM audit_log WHERE action = 'chat_denied' ORDER BY id DESC").fetchone()
    assert (row["action"], row["intent"], row["role"]) == ("chat_denied", "report", "employee")


def test_chat_gives_pm_a_project_report_and_ops_a_full_report(client: TestClient) -> None:
    pm = client.post("/chat", json={"question": "monthly report please"}, headers=auth(client, "pm")).json()
    assert pm["report"] and "income" not in pm["report"] and not pm["denied"]
    ops = client.post("/chat", json={"question": "monthly report please"},
                      headers=auth(client, "ops_manager")).json()
    assert ops["report"]["income"] > 0 and "Net" in ops["text"]


def test_chat_subscription_question_is_role_checked(client: TestClient) -> None:
    question = {"question": "Which subscriptions can we cancel?"}
    assert client.post("/chat", json=question, headers=auth(client, "pm")).json()["denied"]
    ops = client.post("/chat", json=question, headers=auth(client, "ops_manager")).json()
    assert ops["analysis"] and "Cancel Zoom" in ops["text"] and "recommendations only" in ops["text"]


def test_smalltalk_does_not_call_any_agent(client: TestClient) -> None:
    body = client.post("/chat", json={"question": "hello"}, headers=auth(client, "employee")).json()
    assert body["intent"] == "smalltalk" and "company rules" in body["text"]


def test_history_and_audit_store_redacted_text(client: TestClient, cfg: Config) -> None:
    question = "Email anna.k@example.com or call +49 170 1234567 about DE89 3704 0044 0532 0130 00"
    client.post("/chat", json={"question": question, "session_id": "pii"}, headers=auth(client, "owner"))
    db = connect(cfg.path("sqlite"))
    stored = db.execute(
        "SELECT content FROM chat_history WHERE session_id = 'pii' AND role = 'user'").fetchone()[0]
    audited = db.execute(
        "SELECT question FROM audit_log WHERE question LIKE '%Email%' ORDER BY id DESC").fetchone()[0]
    for text in (stored, audited):
        assert "[EMAIL]" in text and "[PHONE]" in text and "[IBAN]" in text
        assert "anna.k" not in text and "1234567" not in text and "0532" not in text


def test_every_chat_is_audited_with_intent(client: TestClient, cfg: Config) -> None:
    client.post("/chat", json={"question": "audit me please", "session_id": "audit-test"},
                headers=auth(client, "pm"))
    row = connect(cfg.path("sqlite")).execute(
        "SELECT user_id, role, intent, action FROM audit_log WHERE question = 'audit me please'").fetchone()
    assert (row["user_id"], row["role"], row["intent"], row["action"]) == ("E003", "pm", "knowledge", "chat")


def test_feedback_is_saved_only_for_own_answers(client: TestClient, cfg: Config) -> None:
    headers = auth(client, "employee")
    chat_id = client.post("/chat", json={"question": "hi there"}, headers=headers).json()["chat_id"]
    assert client.post("/feedback", json={"chat_id": chat_id, "rating": 1}, headers=headers).status_code == 200
    assert client.post("/feedback", json={"chat_id": chat_id, "rating": 5}, headers=headers).status_code == 422
    other = auth(client, "pm")
    assert client.post("/feedback", json={"chat_id": chat_id, "rating": -1}, headers=other).status_code == 404
    db = connect(cfg.path("sqlite"))
    count = db.execute("SELECT COUNT(*) FROM feedback WHERE chat_id = ?", (chat_id,)).fetchone()[0]
    assert count == 1


def test_invalid_inputs_are_rejected(client: TestClient) -> None:
    headers = auth(client, "owner")
    assert client.post("/chat", json={"question": ""}, headers=headers).status_code == 422
    assert client.post("/chat", json={"question": "x" * 2001}, headers=headers).status_code == 422
    assert client.get("/report?period=daily", headers=headers).status_code == 422


# ------------------------------------------------------------------ voice
def test_tts_returns_audio_or_a_clear_error(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    headers = auth(client, "employee")
    monkeypatch.setattr("nexora.api.main.synthesize", lambda text: b"RIFFfake")
    ok = client.post("/voice/tts", json={"text": "Hello"}, headers=headers)
    assert ok.status_code == 200 and ok.headers["content-type"] == "audio/wav" and ok.content == b"RIFFfake"

    def broken(_: str) -> bytes:
        raise VoiceUnavailableError("no voice")

    monkeypatch.setattr("nexora.api.main.synthesize", broken)
    bad = client.post("/voice/tts", json={"text": "Hello"}, headers=headers)
    assert bad.status_code == 503 and "no voice" in bad.json()["detail"]


def test_empty_database_gives_a_clear_login_error(tmp_path: Path) -> None:
    config = Config(copy.deepcopy(dict(load_config())))
    config["paths"].update(sqlite=str(tmp_path / "empty.sqlite"))
    app = create_app(config, Orchestrator(config, FakeAssistant(), OfflineLLM()))
    resp = TestClient(app).post("/login", json={"employee_id": "E001", "password": "x"})
    assert resp.status_code == 503 and "make load" in resp.json()["detail"]


def test_action_confirmation_is_logged_and_role_checked(client: TestClient, cfg: Config) -> None:
    body = {"tool": "Datadog", "action": "cancel", "detail": "Cancel Datadog"}
    assert client.post("/actions/confirm", json=body, headers=auth(client, "pm")).status_code == 403
    ok = client.post("/actions/confirm", json=body, headers=auth(client, "ops_manager"))
    assert ok.status_code == 200 and "does not cancel" in ok.json()["note"]
    row = connect(cfg.path("sqlite")).execute(
        "SELECT user_id, detail FROM audit_log WHERE action = 'action_confirmed'").fetchone()
    assert row["user_id"] == "E002" and "cancel Datadog" in row["detail"]


def test_register_creates_employee_and_logs_in(client: TestClient) -> None:
    body = {"name": "New Person", "email": "New.Person@Example.com", "password": "longenough1"}
    resp = client.post("/register", json=body)
    assert resp.status_code == 201
    assert resp.json()["user"]["role"] == "employee"  # a role in the body is ignored
    token = resp.json()["token"]
    assert client.get("/tasks", headers={"Authorization": f"Bearer {token}"}).status_code == 200
    # log in with the email (any case) and with the generated id
    by_email = {"employee_id": "new.person@example.com", "password": "longenough1"}
    assert client.post("/login", json=by_email).status_code == 200
    assert client.post("/login", json={"employee_id": resp.json()["user"]["employee_id"],
                                       "password": "longenough1"}).status_code == 200


def test_register_rejects_bad_input(client: TestClient) -> None:
    ok = {"name": "Dup", "email": "dup@example.com", "password": "longenough1"}
    assert client.post("/register", json=ok).status_code == 201
    assert client.post("/register", json=ok).status_code == 409
    assert client.post("/register", json={**ok, "email": "nope"}).status_code == 422
    assert client.post("/register", json={**ok, "email": "x@example.com", "password": "short"}).status_code == 422
    sneaky = client.post("/register", json={**ok, "email": "r@example.com", "role": "owner"})
    assert sneaky.json()["user"]["role"] == "employee"


def test_chats_are_saved_listed_and_reopened(client: TestClient) -> None:
    def auth(role: str) -> dict[str, str]:
        token = client.post("/login", json={"employee_id": USERS[role], "password": PASSWORD}).json()["token"]
        return {"Authorization": f"Bearer {token}"}

    owner, employee = auth("owner"), auth("employee")
    client.post("/chat", json={"question": "Give me the monthly report", "session_id": "mem-a"}, headers=owner)
    client.post("/chat", json={"question": "How many vacation days?", "session_id": "mem-b"}, headers=owner)
    chats = client.get("/chats", headers=owner).json()
    ids = [c["session_id"] for c in chats]
    assert ids.index("mem-b") < ids.index("mem-a")  # newest first
    assert next(c for c in chats if c["session_id"] == "mem-a")["title"] == "Give me the monthly report"
    messages = client.get("/chats/mem-a", headers=owner).json()
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[1]["report"] and messages[1]["chat_id"] > 0  # report cards come back with the text
    # another user cannot see or open it
    assert "mem-a" not in [c["session_id"] for c in client.get("/chats", headers=employee).json()]
    assert client.get("/chats/mem-a", headers=employee).json() == []
    assert client.get("/chats").status_code == 401
