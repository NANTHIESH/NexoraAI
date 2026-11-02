# Evaluation protocol

All numbers in the README are generated from `evaluation/results/*.json` by `python -m scripts.fill_readme`. If a result file does not exist the README says "not run yet". Nothing is typed in by hand.

## A. Rules assistant vs. no-documents baseline (`evaluation/eval_rules.py`)

- **Questions:** the 15 in `evaluation/qa_set.json` (12 answerable, 3 not answerable).
- **Systems:** A = Nexora (retrieval + strict prompt + refusal). B = the same model with no documents. Same model, temperature 0, fixed seed.
- **Answerable question passes** if the answer contains every entry of `expected_answer_contains` (case-insensitive; numbers match as whole numbers, so "5" is not found in "25"; `any_of` entries accept alternatives), contains none of `forbidden_contains`, and, for System A only, cites the correct `source_doc` in a `[Title, Section]` citation that names a retrieved document.
- **Unanswerable question passes** if System A refuses (the refusal text, or the model said `NOT_FOUND`), or System B explicitly says it does not know / has no information (regular expression in the script). Any other answer is a hallucination and a fail.
- **Reported:** per question, passed count, accuracy, citation accuracy (answerable questions that cite the right document), refusal accuracy, hallucination count, mean/median latency, and with `--runs N` the mean and standard deviation over runs.
- **Extra runs:** `--semantic-only` runs System A with the Phase 3 retrieval (no hybrid, no reranker) for a before/after comparison; `--model qwen2.5:7b` runs another Ollama model.
- **Needs** Ollama and `make index`; it refuses to run on the extractive fallback because that would not measure the language model.
- **Target:** accuracy above 90% (at least 14 of 15). Each question is worth 6.7 points.

Known limits: 15 questions is a small sample, one wrong answer moves the score by 6.7 points; the questions were written by the same team that wrote the documents; B's "does not know" check is a pattern match and can misjudge hedged answers; the refusal threshold and prompts were tuned on separate development questions (`tests/fixtures/dev_questions.json`), never on this set, but the 15 questions were visible to the developers.

## Retrieval on development questions (`evaluation/eval_retrieval.py`)

28 answerable and 10 unanswerable development questions with the expected source document; semantic vs. hybrid vs. reranker modes; hit@1/3/5, MRR, and how often the threshold separates answerable from unanswerable questions. Retrieval only. Used to choose the retrieval settings, so it is not an independent test.

## B. Detection recall and false alerts (`evaluation/eval_detection.py`)

- For each seed (42, 7, 123) the data is generated fresh, the five detectors run with the `config.yaml` thresholds, and flagged detection keys are compared with `ground_truth.json`.
- **Recall** = planted errors found / 20. **False alerts** = flags that match no planted error (listed). **Precision** = correct flags / all flags. Per-type breakdown and the list of decoys that were wrongly flagged are in the JSON.
- **Savings** = sum of monthly savings from the flagged subscription problems (no double counting for a tool that is cancelled), plus one-off refunds.
- **Targets:** recall >= 80% (16 of 20), false alerts <= 3, precision >= 80%.
- **Honest caveat:** the generator and the detectors were built together; thresholds were not tuned per seed, but good scores show consistency with the planted patterns, not real-world accuracy.

## C. Time and value (`evaluation/eval_time.py`, `eval_satisfaction.py`)

- **Time:** the team measures three tasks by stopwatch, manual and with Nexora, and enters them with `python -m evaluation.eval_time record`. The file stays `null` until then. Target: 50% less time.
- **Satisfaction:** thumbs up / all thumbs from the `feedback` table (`python -m evaluation.eval_satisfaction`). "Not measured" until real feedback exists. Target: above 85%.
- **Savings:** reported in section B.

## Scoring change log (kept for honesty)

- **First run, strict digits:** the first full run of evaluation A used the digit alone as the expected fact (for example `"2"`). It scored Nexora 13/15 (86.7%): question 4 was counted as a miss although the answer was "Two approvals are needed before a pull request can be merged." with the right citation, and question 10 was a genuine failure (see below).
- **Change:** number questions now accept the digit or the word (`"2"` or `"two"`, and so on, for every numeric question, for both systems), recorded in `qa_set.json`. The change was made after seeing the first run, because the strict rule contradicted the stated rule "the answer contains the expected fact". Both evaluations (System A with hybrid + reranker, System A semantic-only, and the baseline) were re-run with the corrected rule; the numbers in the README come from those re-runs.
- **Question 10** ("Can I send customer data to my personal email?") is a real failure that is deliberately not tuned away: retrieval ranked the right passage first (cosine 0.58, reranker score 8.2), but the 3B model answered `NOT_FOUND`, so Nexora refused a question the documents answer. Prompts, thresholds and retrieval settings were not changed after seeing the evaluation results.
