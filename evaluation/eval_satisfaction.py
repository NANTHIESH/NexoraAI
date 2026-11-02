"""User satisfaction from the thumbs up/down stored in the feedback table (never invented).

Run: python -m evaluation.eval_satisfaction
Writes evaluation/results/satisfaction.json. With no feedback the status is "not measured".
"""

from __future__ import annotations

import json
import sqlite3
import sys
from datetime import UTC, datetime
from typing import Any

from nexora.config import load_config
from nexora.db.session import connect

TARGET = 0.85
MIN_FEEDBACK_FOR_STATEMENT = 1


def measure(conn: sqlite3.Connection) -> dict[str, Any]:
    """Satisfaction = thumbs up / all thumbs. ``satisfaction`` is None when there is no feedback."""
    total, up = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(rating = 1), 0) FROM feedback").fetchone()
    satisfaction = round(up / total, 3) if total >= MIN_FEEDBACK_FOR_STATEMENT else None
    return {"run_at": datetime.now(UTC).isoformat(timespec="seconds"), "target": TARGET,
            "feedback_count": total, "thumbs_up": int(up), "thumbs_down": int(total - up),
            "satisfaction": satisfaction,
            "status": "not measured" if satisfaction is None else ("meets target" if satisfaction >= TARGET
                                                                    else "below target")}


def main() -> int:
    """CLI entry point."""
    cfg = load_config()
    result = measure(connect(cfg.path("sqlite")))
    path = cfg.path("eval_results") / "satisfaction.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Feedback: {result['feedback_count']} ratings -> {result['status']}. Saved {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
