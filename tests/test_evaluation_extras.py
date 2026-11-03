"""Tests for the extra evaluations: figure support, the regression gate, production metrics, injection phrases."""

from __future__ import annotations

import json
from pathlib import Path

from evaluation.check_gate import CHECKS, run
from evaluation.eval_faithfulness import numbers, score
from evaluation.eval_production import compute
from evaluation.eval_safety import PARAPHRASED_INJECTIONS, TEXTBOOK_INJECTIONS, prompt_injection

from nexora.db.session import connect
from nexora.rag.chunking import Chunk
from nexora.rag.generator import Answer
from nexora.rag.vectorstore import Hit
from nexora.security import audit


def _hit(text: str, source: str = "leave.md") -> Hit:
    return Hit(Chunk("c1", source, "Leave Policy", "1", "2026-01-01", "current", "HR", "Vacation", None, text), 0.8)


# ------------------------------------------------------------------ figures in answers
def test_numbers_ignore_citations_and_source_mentions() -> None:
    assert numbers("According to SOURCE 1, employees get 25 days. [Leave Policy 2, Vacation]") == {"25"}
    assert numbers("The budget is 1,000 EUR") == {"1000"}


def test_a_figure_that_is_in_no_passage_is_flagged() -> None:
    hit = _hit("Full-time employees get 25 days of vacation.")
    cite = ["[Leave Policy, Vacation]"]
    good = Answer("25 days. [Leave Policy, Vacation]", False, "llm", cite, ["leave.md"], [hit], 0.8)
    bad = Answer("30 days. [Leave Policy, Vacation]", False, "llm", cite, ["leave.md"], [hit], 0.8)
    assert score(good, ["leave.md"])["figures_supported"] is True
    assert score(good, ["leave.md"])["right_document"] is True
    flagged = score(bad, ["leave.md"])
    assert flagged["figures_supported"] is False and flagged["cited_supports_figures"] is False


def test_refusals_are_not_scored_for_figures() -> None:
    refusal = Answer("I could not find this.", True, "llm", sources=[_hit("Nothing here.")])
    result = score(refusal, None)
    assert result["refused"] and result["figures_supported"] is None and result["right_document"] is None


# ------------------------------------------------------------------ regression gate
def test_gate_fails_when_results_are_missing(tmp_path: Path) -> None:
    outcome = run(tmp_path)
    assert len(outcome) == len(CHECKS) and not any(ok for _, ok, _ in outcome)
    assert all("missing" in why for _, _, why in outcome)


def test_gate_compares_each_number_with_its_limit(tmp_path: Path) -> None:
    def write(passed: int) -> None:
        (tmp_path / "rules_eval.json").write_text(json.dumps({"system_a": {"runs": [{"passed": passed}]}}))

    write(14)
    assert next(ok for name, ok, _ in run(tmp_path) if name.startswith("Rules assistant"))
    write(13)  # one question worse: the gate must notice
    assert not next(ok for name, ok, _ in run(tmp_path) if name.startswith("Rules assistant"))


# ------------------------------------------------------------------ production metrics
def test_production_metrics_count_actions_and_feedback(tmp_path: Path) -> None:
    conn = connect(tmp_path / "p.sqlite")
    for action, detail in (("chat", "method=llm mode=llm"), ("chat", "method=keywords mode=extractive"),
                           ("chat_refused", "method=llm mode=llm"), ("chat_denied", "method=llm mode=llm")):
        audit.log_event(conn, "E001", "owner", action, detail=detail)
    audit.log_event(conn, "E001", "owner", "login")
    audit.log_event(conn, "E001", "owner", "login_failed")
    conn.execute("INSERT INTO chat_history (user_id, session_id, role, content, intent, created_at) "
                 "VALUES ('E001', 's1', 'assistant', 'Which one?', 'clarify', 'now')")
    conn.execute("INSERT INTO chat_history (user_id, session_id, role, content, intent, created_at) "
                 "VALUES ('E001', 's1', 'assistant', 'Answer', 'report', 'now')")
    conn.execute("INSERT INTO feedback (chat_id, user_id, rating, created_at) VALUES (1, 'E001', 1, 'now')")
    conn.commit()
    metrics = compute(conn)
    conn.close()
    assert metrics["questions_asked"] == 4
    assert metrics["answered"]["k"] == 2 and metrics["refused_no_source_found"]["k"] == 1
    assert metrics["denied_by_role"]["k"] == 1
    assert metrics["answers_in_extractive_mode"]["k"] == 1 and metrics["routed_by_keywords_only"]["k"] == 1
    assert metrics["clarifying_questions"]["k"] == 1 and metrics["login_failures"]["k"] == 1
    assert metrics["feedback"]["thumbs_up"] == 1


# ------------------------------------------------------------------ prompt-injection guard
def test_guard_removes_every_textbook_injection() -> None:
    groups = prompt_injection()
    assert len(groups["prompt injection, textbook phrases"]) == len(TEXTBOOK_INJECTIONS)
    assert not any(a["success"] for a in groups["prompt injection, textbook phrases"])


def test_paraphrased_injections_are_a_known_gap_that_the_evaluation_reports() -> None:
    """Documented weakness: if the guard ever learns to catch these, update the gate limit in check_gate.py."""
    survivors = [a for a in prompt_injection()["prompt injection, paraphrased to avoid the patterns"] if a["success"]]
    assert len(survivors) <= len(PARAPHRASED_INJECTIONS)
