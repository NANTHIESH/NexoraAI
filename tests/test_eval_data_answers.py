"""The data-answer evaluation doubles as a regression test: every computed answer must match the raw data."""

from __future__ import annotations

from evaluation.eval_data_answers import SEEDS, run_seed


def test_every_computed_answer_matches_the_independent_ground_truth() -> None:
    results = run_seed(SEEDS[0])
    assert len(results) > 40  # income, spending, tasks, headcount, subscriptions, biggest expense
    failed = [(r["question"], r["missing"]) for r in results if not r["passed"]]
    assert not failed, failed


def test_the_check_can_fail() -> None:
    """A wrong expectation must be reported as a failure (the check is not vacuous)."""
    results = run_seed(SEEDS[0])
    first = results[0]
    other_numbers = results[len(results) // 2]["expected"]
    assert not all(e.lower() in first["text"] for e in other_numbers)
