"""SQLite connection helper that applies the schema on first use."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: Path) -> sqlite3.Connection:
    """Open (and create if needed) the database at ``path`` with the schema applied."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each request opens its own connection, but FastAPI may create it in a worker thread and use it
    # from the event loop, so the same-thread check is relaxed. A connection is never shared.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    return conn
