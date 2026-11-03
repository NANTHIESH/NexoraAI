"""Evaluation H: how the assistant behaves in real use, from its own logs.

Run: python -m evaluation.eval_production
Reads the tables the application already writes (audit_log, chat_history, feedback) in data/db/nexora.sqlite.
Reported with 95 % intervals: refusal and denial rates, clarifying questions, how often answers came from the
fallbacks (extractive mode, keyword routing), login failures and user feedback. This is monitoring, not a test:
the numbers describe whoever used this database, so they include the team's own testing.
Latency is not logged yet, so it is not reported here (the answer evaluation reports it).
Writes evaluation/results/production_metrics.json.
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from typing import Any

from evaluation.stats import wilson_interval
from nexora.config import load_config


def _share(k: int, n: int) -> dict[str, Any]:
    low, high = wilson_interval(k, n)
    return {"k": k, "n": n, "share": round(k / n, 4) if n else None, "ci95": [round(low, 4), round(high, 4)]}


def compute(conn: sqlite3.Connection) -> dict[str, Any]:
    """All usage metrics from one database connection."""
    actions = dict(conn.execute("SELECT action, COUNT(*) FROM audit_log GROUP BY action").fetchall())
    answered, refused, denied = (actions.get(a, 0) for a in ("chat", "chat_refused", "chat_denied"))
    questions = answered + refused + denied
    details = [r[0] or "" for r in conn.execute(
        "SELECT detail FROM audit_log WHERE action IN ('chat', 'chat_refused', 'chat_denied')")]
    intents = dict(conn.execute("SELECT intent, COUNT(*) FROM chat_history WHERE role = 'assistant' "
                                "GROUP BY intent").fetchall())
    replies = sum(intents.values())
    logins, failed = actions.get("login", 0), actions.get("login_failed", 0)
    ratings = dict(conn.execute("SELECT rating, COUNT(*) FROM feedback GROUP BY rating").fetchall())
    up, down = ratings.get(1, 0), ratings.get(-1, 0)
    return {
        "run_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "note": "usage of this database so far, including the team's own testing; monitoring, not a test",
        "questions_asked": questions,
        "users": conn.execute("SELECT COUNT(DISTINCT user_id) FROM chat_history").fetchone()[0],
        "conversations": conn.execute("SELECT COUNT(DISTINCT session_id) FROM chat_history").fetchone()[0],
        "answered": _share(answered, questions),
        "refused_no_source_found": _share(refused, questions),
        "denied_by_role": _share(denied, questions),
        "clarifying_questions": _share(intents.get("clarify", 0), replies),
        "answers_by_intent": {k or "unknown": v for k, v in sorted(intents.items(), key=lambda kv: -kv[1])},
        "answers_in_extractive_mode": _share(sum("mode=extractive" in d for d in details), len(details)),
        "routed_by_keywords_only": _share(sum("method=keywords" in d for d in details), len(details)),
        "login_failures": _share(failed, logins + failed),
        "registrations": actions.get("register", 0),
        "feedback": {"thumbs_up": up, "thumbs_down": down, "positive": _share(up, up + down)},
        "confirmed_recommendations": actions.get("action_confirmed", 0),
    }


def main() -> int:
    """CLI entry point."""
    cfg = load_config()
    path = cfg.path("sqlite")
    if not path.exists():
        print(f"No database at {path}. Run `make data load` first.", file=sys.stderr)
        return 1
    conn = sqlite3.connect(path)
    try:
        result = compute(conn)
    finally:
        conn.close()
    (cfg.path("eval_results") / "production_metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    for key in ("answered", "refused_no_source_found", "denied_by_role", "clarifying_questions",
                "answers_in_extractive_mode", "routed_by_keywords_only", "login_failures"):
        s = result[key]
        print(f"{key:28} {s['k']}/{s['n']}" + (f" = {s['share']:.0%}" if s["n"] else ""))
    fb = result["feedback"]
    print(f"feedback: {fb['thumbs_up']} up, {fb['thumbs_down']} down; {result['questions_asked']} questions, "
          f"{result['users']} users, {result['conversations']} conversations")
    return 0


if __name__ == "__main__":
    sys.exit(main())
