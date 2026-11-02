"""Phase 6 tests: conversation memory, outdated-document warning, voice input."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from scripts.generate_data import generate
from scripts.load_db import load_db

from nexora.agents.memory import Turn, load_history, looks_like_follow_up, rewrite_question
from nexora.agents.orchestrator import Orchestrator
from nexora.api.main import create_app
from nexora.config import Config, load_config
from nexora.db.session import connect
from nexora.llm.ollama_client import OllamaUnavailableError
from nexora.rag.chunking import Chunk
from nexora.rag.generator import Answer, outdated_warning
from nexora.rag.vectorstore import Hit
from nexora.voice.stt import transcribe
from nexora.voice.tts import VoiceUnavailableError

PASSWORD = load_config().auth.demo_password
HISTORY = [Turn("What is the monthly report?", "Income 95,000 EUR ..."),
           Turn("How many days can I work remotely?", "Up to 3 days per week.")]


class ScriptedLLM:
    """Replies by looking at which prompt it receives."""

    def __init__(self, rewrite: str | Exception = "") -> None:
        self.rewrite, self.rewrite_calls = rewrite, 0

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        if system.startswith("Rewrite the user"):
            self.rewrite_calls += 1
            if isinstance(self.rewrite, Exception):
                raise self.rewrite
            return self.rewrite
        return "{}"


# ------------------------------------------------------------------ follow-up detection and rewriting
@pytest.mark.parametrize(("question", "expected"), [
    ("And for the whole year?", True), ("What about that?", True), ("Why is it so high?", True),
    ("Who approves it?", True), ("hours?", True),
    ("How many vacation days do full-time employees get per year in total?", False)])
def test_follow_up_detection(question: str, expected: bool) -> None:
    assert looks_like_follow_up(question, HISTORY) is expected


def test_nothing_is_a_follow_up_without_history() -> None:
    assert not looks_like_follow_up("And for the year?", [])
    assert rewrite_question("And for the year?", [], ScriptedLLM("x")) == "And for the year?"


def test_model_rewrites_a_follow_up_into_a_standalone_question() -> None:
    llm = ScriptedLLM('"Give me the yearly report"\nExtra line the model should not add')
    assert rewrite_question("and for the year?", HISTORY, llm) == "Give me the yearly report"
    assert llm.rewrite_calls == 1


def test_standalone_long_question_is_not_sent_to_the_model() -> None:
    llm = ScriptedLLM("should not be used")
    question = "How many vacation days do full-time employees get per year in total please?"
    assert rewrite_question(question, HISTORY, llm) == question and llm.rewrite_calls == 0


@pytest.mark.parametrize("reply", [OllamaUnavailableError("http://x"), "", "x" * 600])
def test_bad_model_output_falls_back_to_context_prefix(reply: str | Exception) -> None:
    out = rewrite_question("And for the year?", HISTORY, ScriptedLLM(reply))
    assert out == "How many days can I work remotely? And for the year?"


def test_without_a_model_only_referring_questions_get_the_previous_question() -> None:
    assert rewrite_question("and for the year?", HISTORY, None).startswith("How many days can I work remotely?")
    unrelated = "Is the office closed at Christmas?"
    assert rewrite_question(unrelated, HISTORY, None) == unrelated  # short but does not refer back


def test_history_returns_last_turns_oldest_first(tmp_path: Path) -> None:
    conn = connect(tmp_path / "h.sqlite")
    for n in range(7):
        for role in ("user", "assistant"):
            conn.execute("INSERT INTO chat_history (user_id, session_id, role, content, created_at) "
                         "VALUES ('E1', 's', ?, ?, 'now')", (role, f"{role} {n}"))
    conn.execute("INSERT INTO chat_history (user_id, session_id, role, content, created_at) "
                 "VALUES ('E1', 'other', 'user', 'elsewhere', 'now')")
    conn.commit()
    history = load_history(conn, "E1", "s", turns=5)
    assert [t.question for t in history] == [f"user {n}" for n in range(2, 7)]
    assert history[-1].answer == "assistant 6"
    assert load_history(conn, "E2", "s", turns=5) == []


# ------------------------------------------------------------------ memory through the API
@pytest.fixture(scope="module")
def cfg(tmp_path_factory: pytest.TempPathFactory) -> Config:
    base = tmp_path_factory.mktemp("p6")
    generate(42, base / "gen")
    config = Config(copy.deepcopy(dict(load_config())))
    config["paths"].update(generated=str(base / "gen"), sqlite=str(base / "p6.sqlite"))
    config["auth"]["bcrypt_rounds"] = 4
    load_db(config)
    return config


class OfflineLLM:
    model = "fake"

    def is_ready(self) -> bool:
        return False

    def chat(self, *_: object, **__: object) -> str:
        raise OllamaUnavailableError("http://localhost:1")


class EchoAssistant:
    def ask(self, question: str, include_superseded: bool | None = None) -> Answer:
        return Answer(f"ANSWER[{question}]", False, "extractive")


@pytest.fixture(scope="module")
def client(cfg: Config) -> TestClient:
    return TestClient(create_app(cfg, Orchestrator(cfg, EchoAssistant(), OfflineLLM())))


def headers(client: TestClient, employee_id: str = "E002") -> dict[str, str]:
    token = client.post("/login", json={"employee_id": employee_id, "password": PASSWORD}).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def test_follow_up_uses_the_previous_question_within_a_session(client: TestClient) -> None:
    h = headers(client)
    first = client.post("/chat", json={"question": "monthly report please", "session_id": "m1"}, headers=h).json()
    assert first["report"]["period"] == "monthly" and first["rewritten_question"] is None
    second = client.post("/chat", json={"question": "and for the year?", "session_id": "m1"}, headers=h).json()
    assert second["intent"] == "report" and second["report"]["period"] == "yearly"
    assert second["rewritten_question"] == "monthly report please and for the year?"


def test_memory_does_not_leak_between_sessions_or_users(client: TestClient) -> None:
    client.post("/chat", json={"question": "monthly report please", "session_id": "m2"}, headers=headers(client))
    other_session = client.post("/chat", json={"question": "and for the year?", "session_id": "m3"},
                                headers=headers(client)).json()
    assert other_session["rewritten_question"] is None and other_session["intent"] == "knowledge"
    other_user = client.post("/chat", json={"question": "and for the year?", "session_id": "m2"},
                             headers=headers(client, "E001")).json()
    assert other_user["rewritten_question"] is None


def test_greetings_are_never_rewritten(client: TestClient) -> None:
    h = headers(client)
    client.post("/chat", json={"question": "monthly report please", "session_id": "m4"}, headers=h)
    thanks = client.post("/chat", json={"question": "thanks!", "session_id": "m4"}, headers=h).json()
    assert thanks["intent"] == "smalltalk" and thanks["rewritten_question"] is None


def test_rewrite_is_recorded_in_the_audit_log(client: TestClient, cfg: Config) -> None:
    h = headers(client)
    client.post("/chat", json={"question": "monthly report please", "session_id": "m5"}, headers=h)
    client.post("/chat", json={"question": "and for the year, please?", "session_id": "m5"}, headers=h)
    detail = connect(cfg.path("sqlite")).execute(
        "SELECT detail FROM audit_log WHERE question LIKE 'and for the year, please%'").fetchone()[0]
    assert "rewritten=monthly report please and for the year, please?" in detail


# ------------------------------------------------------------------ outdated-document warning
def hit(source: str, title: str, effective: str) -> Hit:
    return Hit(Chunk(f"{source}::0", source, title, "1", effective, "current", "o", "S", None, "t"), 0.9)


TODAY = date(2026, 11, 2)


def test_old_cited_document_gets_a_warning_with_its_age() -> None:
    warning = outdated_warning([hit("a.md", "Old Policy", "2024-09-01")], ["a.md"], 548, TODAY)
    assert warning and "Old Policy" in warning and "2024-09-01" in warning and "check" in warning


def test_recent_document_gets_no_warning() -> None:
    assert outdated_warning([hit("a.md", "New Policy", "2026-03-01")], ["a.md"], 548, TODAY) is None


def test_only_the_cited_document_is_judged() -> None:
    hits = [hit("old.md", "Old", "2020-01-01"), hit("new.md", "New", "2026-01-01")]
    assert outdated_warning(hits, ["new.md"], 548, TODAY) is None
    assert "Old" in outdated_warning(hits, [], 548, TODAY)  # no citation: judge the best hit


def test_current_corpus_has_no_stale_documents_but_a_stricter_limit_flags_them() -> None:
    from nexora.rag.loaders import load_document

    folder = load_config().path("documents")
    docs = [load_document(p) for p in sorted(folder.iterdir()) if p.suffix in (".md", ".docx", ".pdf")]
    current = [d for d in docs if d.status == "current"]
    ages = [(TODAY - date.fromisoformat(d.effective_date)).days for d in current]
    assert max(ages) < load_config().retrieval.outdated_after_days
    assert max(ages) > 365  # the 2025-06 policies would be flagged by a 12-month limit


# ------------------------------------------------------------------ voice input
def test_stt_endpoint_returns_text_and_requires_login(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("nexora.api.main.transcribe", lambda audio: f"heard {len(audio)} bytes")
    assert client.post("/voice/stt", content=b"abc").status_code == 401
    ok = client.post("/voice/stt", content=b"abcdef", headers=headers(client, "E005"))
    assert ok.status_code == 200 and ok.json() == {"text": "heard 6 bytes"}


def test_stt_endpoint_reports_missing_voice_support_clearly(client: TestClient,
                                                           monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(_: bytes) -> str:
        raise VoiceUnavailableError("Speech-to-text is not installed. Run: pip install -r requirements-voice.txt")

    monkeypatch.setattr("nexora.api.main.transcribe", unavailable)
    resp = client.post("/voice/stt", content=b"abc", headers=headers(client))
    assert resp.status_code == 503 and "requirements-voice.txt" in resp.json()["detail"]


def test_stt_rejects_empty_and_oversized_audio() -> None:
    with pytest.raises(VoiceUnavailableError, match="No audio"):
        transcribe(b"")
    with pytest.raises(VoiceUnavailableError, match="too large"):
        transcribe(b"x" * (10 * 1024 * 1024 + 1))


def test_text_to_speech_and_speech_to_text_round_trip() -> None:
    pytest.importorskip("faster_whisper")
    from nexora.voice.tts import synthesize

    try:
        text = transcribe(synthesize("Full-time employees get twenty five days of paid vacation per year."))
    except VoiceUnavailableError as exc:  # no system voice or no offline model on this machine
        pytest.skip(str(exc))
    assert "25" in text or "twenty" in text.lower()
    assert "vacation" in text.lower()
