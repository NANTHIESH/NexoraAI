"""SQLite connection helper that applies the schema once per database file."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
_SCHEMA_SQL = SCHEMA_PATH.read_text(encoding="utf-8")
_initialised: set[str] = set()
_lock = threading.Lock()


def connect(path: Path) -> sqlite3.Connection:
    """Open (and create if needed) the database at ``path`` with the schema applied.

    The schema script runs once per file per process (not on every request), and the connection
    uses WAL mode so readers do not block the writer.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Each request opens its own connection, but FastAPI may create it in a worker thread and use it
    # from the event loop, so the same-thread check is relaxed. A connection is never shared.
    existed = path.exists()
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    key = str(path.resolve())
    if key not in _initialised or not existed:
        with _lock:
            conn.executescript(_SCHEMA_SQL)
            _initialised.add(key)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn
