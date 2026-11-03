"""The statistics helpers behind the evaluation report (confidence intervals, paired test, percentiles)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evaluation.eval_statistics import compute
from evaluation.stats import mcnemar_exact, paired_counts, percentile, wilson_interval


def test_wilson_interval_matches_known_values() -> None:
    low, high = wilson_interval(14, 15)  # 93 % on 15 questions: a wide interval
    assert (round(low, 3), round(high, 3)) == (0.702, 0.988)
    assert wilson_interval(0, 0) == (0.0, 1.0)
    low, high = wilson_interval(20, 20)  # all right still leaves doubt
    assert high == pytest.approx(1.0) and 0.83 < low < 0.85


def test_interval_narrows_with_more_questions() -> None:
    small = wilson_interval(42, 50)
    large = wilson_interval(840, 1000)  # same 84 %
    assert (large[1] - large[0]) < (small[1] - small[0]) / 3


def test_mcnemar_uses_only_the_disagreements() -> None:
    assert mcnemar_exact(15, 5) == pytest.approx(0.0414, abs=1e-3)  # the lecture's example: B wins 15, A wins 5
    assert mcnemar_exact(5, 3) > 0.5  # 5 vs 3: nothing can be concluded
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(1, 0) == 1.0  # one question of difference is never evidence
    assert mcnemar_exact(11, 0) < 0.01


def test_paired_counts_ignore_questions_both_get_right_or_wrong() -> None:
    a = {1: True, 2: True, 3: False, 4: False, 5: True}
    b = {1: True, 2: False, 3: False, 4: True, 6: True}
    assert paired_counts(a, b) == (1, 1)  # question 5 and 6 are not answered by both systems


def test_percentile_interpolates() -> None:
    assert percentile([1, 2, 3, 4], 50) == 2.5
    assert percentile([10.0], 95) == 10.0
    assert percentile([], 50) == 0.0


def test_compute_reads_saved_results_and_never_claims_a_one_question_difference(tmp_path: Path) -> None:
    def system(passed: list[bool]) -> list[dict]:
        run = {"latency_seconds": 1.0}
        return [{"id": i, "a": [{"passed": p, **run}], "b": [{"passed": False, **run}]} for i, p in enumerate(passed)]

    (tmp_path / "rules_eval.json").write_text(json.dumps({"questions": system([True] * 14 + [False])}))
    semantic = {"questions": system([True] * 13 + [False] * 2)}
    (tmp_path / "rules_eval_semantic_only.json").write_text(json.dumps(semantic))
    stats = compute(tmp_path)
    assert stats["answers"]["nexora"]["k"] == 14 and stats["answers"]["nexora_semantic_only"]["k"] == 13
    assert stats["target_check"]["proven"] is False  # 14/15 cannot prove "above 90 %"
    versus = next(p for p in stats["paired"] if p["b"] == "nexora_semantic_only")
    assert versus["only_a_right"] == 1 and versus["conclusive"] is False
