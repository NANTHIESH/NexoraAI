"""Phase 7 tests: question set, scoring rules, detection comparison, time/satisfaction files, README filler."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evaluation import eval_time
from evaluation.eval_detection import compare, evaluate_seed
from evaluation.eval_rules import facts_present, load_questions, says_unknown, score_answer, summarize
from evaluation.eval_satisfaction import measure
from scripts.fill_readme import README, RENDERERS, fill, render_rules, render_time

from nexora.config import load_config
from nexora.db.session import connect
from nexora.rag.generator import Answer
from nexora.rag.loaders import load_document

QUESTIONS = load_questions()
BY_ID = {q["id"]: q for q in QUESTIONS}


# ------------------------------------------------------------------ the question set
def test_question_set_is_the_agreed_fifteen() -> None:
    assert [q["id"] for q in QUESTIONS] == list(range(1, 16))
    assert sum(q["answerable"] for q in QUESTIONS) == 12
    assert {q["id"] for q in QUESTIONS if not q["answerable"]} == {13, 14, 15}
    assert all(set(q) >= {"id", "question", "expected_answer_contains", "source_doc", "answerable"} for q in QUESTIONS)


def test_every_expected_fact_is_in_its_source_document() -> None:
    folder = load_config().path("documents")
    for q in QUESTIONS:
        if not q["answerable"]:
            assert q["source_doc"] is None
            continue
        doc = load_document(folder / q["source_doc"])
        text = " ".join(s.text for s in doc.sections)
        assert doc.status == "current"
        for entry in q["expected_answer_contains"]:
            if isinstance(entry, dict):
                continue  # yes/no style answers
            assert facts_present(text, [entry]) or entry in text.lower(), (q["id"], entry)


# ------------------------------------------------------------------ fact matching
@pytest.mark.parametrize(("text", "expected", "result"), [
    ("Full-time employees get 25 days.", ["25"], True),
    ("Employees get 25 days.", ["5"], False),  # '5' must not match inside '25'
    ("Up to 5 unused days carry over.", ["5"], True),
    ("Payslips go out on the 28th of each month.", ["28"], True),
    ("The budget is 1,000 EUR.", [{"any_of": ["1,000", "1000"]}], True),
    ("The budget is 1000 EUR.", [{"any_of": ["1,000", "1000"]}], True),
    ("No, not after 14:00.", [{"any_of": ["no", "not"]}, "14:00"], True),
    ("Yes, deploy before 14:00.", [{"any_of": ["no", "not"]}, "14:00"], False),
    ("It is a nonsense answer 14:00", [{"any_of": ["no", "not"]}, "14:00"], False),  # 'no' inside 'nonsense'
    ("The Owner must approve.", ["owner"], True),
    ("Owner approval and registration in the subscription list.", ["owner", "subscription list"], True),
    ("Owner approval only.", ["owner", "subscription list"], False),
])
def test_facts_present(text: str, expected: list, result: bool) -> None:
    assert facts_present(text, expected) is result


def answer(text: str, refused: bool = False, cited: list[str] | None = None) -> Answer:
    return Answer(text, refused, "llm", cited_sources=cited or [])


# ------------------------------------------------------------------ scoring rules
def test_system_a_needs_the_fact_and_the_right_citation() -> None:
    q = BY_ID[1]
    right = score_answer(q, answer("25 days.", cited=["06-leave-policy.docx"]), "A")
    wrong_doc = score_answer(q, answer("25 days.", cited=["07-remote-work-policy-v2.md"]), "A")
    no_cite = score_answer(q, answer("25 days."), "A")
    wrong_fact = score_answer(q, answer("30 days.", cited=["06-leave-policy.docx"]), "A")
    assert right["passed"] and not wrong_doc["passed"] and not no_cite["passed"] and not wrong_fact["passed"]
    assert wrong_doc["has_facts"] and wrong_doc["cited_correct_document"] is False


def test_system_b_needs_only_the_fact_and_is_never_cited() -> None:
    scored = score_answer(BY_ID[1], answer("Probably 25 days."), "B")
    assert scored["passed"] and scored["cited_correct_document"] is None


def test_refusing_an_answerable_question_fails() -> None:
    assert not score_answer(BY_ID[1], answer("I could not find this", refused=True), "A")["passed"]


def test_forbidden_text_fails_the_remote_work_question() -> None:
    q = BY_ID[3]
    assert score_answer(q, answer("3 days per week.", cited=[q["source_doc"]]), "A")["passed"]
    assert not score_answer(q, answer("2 days per week.", cited=[q["source_doc"]]), "A")["passed"]
    assert not score_answer(q, answer("3 days now, 2 days before.", cited=[q["source_doc"]]), "A")["passed"]


def test_unanswerable_question_rules_for_both_systems() -> None:
    q = BY_ID[13]
    assert score_answer(q, answer("refusal", refused=True), "A")["passed"]
    made_up = score_answer(q, answer("The bonus is 10% of salary."), "A")
    assert not made_up["passed"] and made_up["hallucination"]
    assert score_answer(q, answer("I don't have that information."), "B")["passed"]
    invented = score_answer(q, answer("Nexora pays a 13th salary in December."), "B")
    assert not invented["passed"] and invented["hallucination"]


@pytest.mark.parametrize("text", ["I don't know.", "I do not have that information.", "I'm not aware of such a policy.",
                                  "I can't find any details.", "There is no information about that."])
def test_baseline_dont_know_patterns(text: str) -> None:
    assert says_unknown(text)


def test_summary_numbers() -> None:
    rows = [
        {"answerable": True, "passed": True, "cited_correct_document": True, "refused": False,
         "hallucination": False, "latency_seconds": 2.0},
        {"answerable": True, "passed": False, "cited_correct_document": False, "refused": False,
         "hallucination": False, "latency_seconds": 4.0},
        {"answerable": False, "passed": True, "cited_correct_document": None, "refused": True,
         "hallucination": False, "latency_seconds": 3.0},
        {"answerable": False, "passed": False, "cited_correct_document": None, "refused": False,
         "hallucination": True, "latency_seconds": 3.0},
    ]
    s = summarize(rows)
    assert (s["passed"], s["accuracy"], s["answerable_accuracy"]) == (2, 0.5, 0.5)
    assert (s["citation_accuracy"], s["refusal_accuracy"], s["hallucinations"]) == (0.5, 0.5, 1)
    assert (s["latency_mean_seconds"], s["latency_median_seconds"]) == (3.0, 3.0)


# ------------------------------------------------------------------ detection evaluation
TRUTH = {"errors": [{"type": "duplicate_charge", "expected_detection_key": "duplicate_charge|A|2026-01"},
                    {"type": "duplicate_charge", "expected_detection_key": "duplicate_charge|B|2026-02"},
                    {"type": "overlap", "expected_detection_key": "overlap|A+B|2026-03"},
                    {"type": "overlap", "expected_detection_key": "overlap|C+D|2026-04"}],
         "decoys": [{"would_be_false_key": "price_increase|AWS|2026-05"}]}


def test_compare_counts_recall_precision_false_alerts_and_decoys() -> None:
    flagged = ["duplicate_charge|A|2026-01", "overlap|A+B|2026-03", "price_increase|AWS|2026-05", "unused|X|2026-01"]
    r = compare(TRUTH, flagged)
    assert (r["errors_inserted"], r["errors_found"], r["recall"]) == (4, 2, 0.5)
    assert (r["flags_total"], r["false_alerts"], r["precision"]) == (4, 2, 0.5)
    assert r["decoys_wrongly_flagged"] == ["price_increase|AWS|2026-05"]
    assert r["per_type"]["duplicate_charge"] == {"inserted": 2, "found": 1, "recall": 0.5}
    assert r["missed_keys"] == ["duplicate_charge|B|2026-02", "overlap|C+D|2026-04"]


def test_compare_with_no_flags_has_no_precision() -> None:
    assert compare(TRUTH, [])["precision"] is None


def test_detection_evaluation_on_a_seed_meets_the_targets() -> None:
    r = evaluate_seed(42, load_config())
    assert r["errors_inserted"] == 20 and r["meets_targets"]
    assert r["estimated_savings_eur"]["monthly"] > 0


# ------------------------------------------------------------------ time and satisfaction
def test_time_template_is_all_null_and_summary_waits_for_both_modes(tmp_path: Path) -> None:
    data = eval_time.template()
    assert all(t["manual_seconds"] is None and t["nexora_seconds"] is None and t["reduction"] is None
               for t in data["tasks"].values())
    eval_time.record(data, "find_rule", "manual", 240, "Ameni", "2026-11-03")
    assert eval_time.summarize(data)["find_rule"]["reduction"] is None  # only one side measured
    eval_time.record(data, "find_rule", "nexora", 30, "Ameni", "2026-11-03")
    s = eval_time.summarize(data)["find_rule"]
    assert s["reduction"] == 0.875 and s["meets_target"] is True
    assert data["tasks"]["find_rule"]["reduction"] == 0.875
    path = eval_time.save(data, tmp_path / "t.json")
    assert eval_time.load(path) == data


@pytest.mark.parametrize(("task", "mode", "seconds"), [("nope", "manual", 5), ("find_rule", "robot", 5),
                                                       ("find_rule", "manual", 0)])
def test_time_record_rejects_invalid_input(task: str, mode: str, seconds: float) -> None:
    with pytest.raises(ValueError):
        eval_time.record(eval_time.template(), task, mode, seconds, "x")


def test_satisfaction_is_not_measured_without_feedback_and_exact_with_it(tmp_path: Path) -> None:
    conn = connect(tmp_path / "s.sqlite")
    assert measure(conn)["status"] == "not measured" and measure(conn)["satisfaction"] is None
    conn.execute("INSERT INTO employees VALUES ('E1','n','e','owner','t','d','2020-01-01',1)")
    conn.execute("INSERT INTO chat_history (id, user_id, session_id, role, content, created_at) "
                 "VALUES (1,'E1','s','assistant','x','now')")
    for rating in (1, 1, 1, -1):
        conn.execute("INSERT INTO feedback (chat_id, user_id, rating, created_at) VALUES (1,'E1',?,'now')", (rating,))
    conn.commit()
    r = measure(conn)
    assert (r["feedback_count"], r["thumbs_up"], r["satisfaction"], r["status"]) == (4, 3, 0.75, "below target")


# ------------------------------------------------------------------ README filler
def fake_results(folder: Path) -> None:
    run = {"questions": 15, "passed": 14, "accuracy": 0.933, "answerable_accuracy": 1.0, "citation_accuracy": 1.0,
           "refusal_accuracy": 0.667, "hallucinations": 1, "latency_mean_seconds": 5.0}
    base = dict(run, passed=6, accuracy=0.4, citation_accuracy=None, hallucinations=3, refusal_accuracy=0.0)
    questions = [{"id": 1, "question": "Q?", "answerable": True, "a": [{"passed": True}], "b": [{"passed": False}]}]
    data = {"model": "m", "runs": 1, "run_at": "2026-11-03T10:00:00", "settings": {"temperature": 0.0},
            "system_a": {"runs": [run]}, "system_b": {"runs": [base]},
            "accuracy_across_runs": {"A": {"mean": 0.933, "stdev": 0.0}, "B": {"mean": 0.4, "stdev": 0.0}},
            "questions": questions}
    (folder / "rules_eval.json").write_text(json.dumps(data), encoding="utf-8")


def test_missing_result_files_say_not_run_yet(tmp_path: Path) -> None:
    for render in RENDERERS.values():
        assert "not run yet" in render(tmp_path)


def test_rules_table_is_generated_from_the_json(tmp_path: Path) -> None:
    fake_results(tmp_path)
    text = render_rules(tmp_path)
    assert "93.3%" in text and "14/15" in text and "40.0%" in text and "**met**" in text
    assert "6/15" in text and "n/a" in text


def test_time_table_shows_not_measured_for_the_template(tmp_path: Path) -> None:
    (tmp_path / "time_eval.json").write_text(json.dumps(eval_time.template()), encoding="utf-8")
    assert render_time(tmp_path).count("not measured") >= 9


def test_fill_replaces_only_marked_blocks_and_is_idempotent(tmp_path: Path) -> None:
    fake_results(tmp_path)
    blocks = "\n".join(f"<!-- BEGIN:{n} -->\nOLD\n<!-- END:{n} -->" for n in RENDERERS)
    text = "intro\n" + blocks + "\noutro"
    filled = fill(text, tmp_path)
    assert filled.startswith("intro\n") and filled.endswith("\noutro") and "OLD" not in filled
    assert "93.3%" in filled and fill(filled, tmp_path) == filled


def test_fill_complains_when_a_marker_is_missing(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="BEGIN:rules"):
        fill("no markers here", tmp_path)


def test_committed_readme_has_every_result_block() -> None:
    text = README.read_text(encoding="utf-8")
    for name in RENDERERS:
        assert f"<!-- BEGIN:{name} -->" in text and f"<!-- END:{name} -->" in text
