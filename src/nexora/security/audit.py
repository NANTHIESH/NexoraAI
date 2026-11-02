"""Audit log: every question, intent, cited sources and action is recorded (PII redacted)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from nexora.security.pii import redact


def log_event(conn: sqlite3.Connection, user_id: str, role: str, action: str, *,
              question: str | None = None, intent: str | None = None,
              sources: list[str] | None = None, detail: str | None = None) -> int:
    """Insert an audit row and return its id.

    ``action`` examples: ``chat``, ``chat_denied``, ``report``, ``subscription_analysis``,
    ``login``, ``login_failed``, ``feedback``, ``tts``.
    """
    with conn:
        cur = conn.execute(
            "INSERT INTO audit_log (ts, user_id, role, question, intent, sources, action, detail) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (datetime.now(UTC).isoformat(timespec="seconds"), user_id, role,
             redact(question) if question else None, intent,
             json.dumps(sources or []), action, redact(detail) if detail else None))
    return int(cur.lastrowid)
