"""Load the generated CSVs (data/generated) into SQLite.

Run: python -m scripts.load_db   (or: make load)
Replaces the contents of the data tables; chat history, feedback, audit log and reports are kept.
"""

from __future__ import annotations

import sys

import pandas as pd

from nexora.config import Config, load_config
from nexora.db.session import connect
from nexora.security.auth import hash_password

TABLES = {  # table -> csv file; load order does not matter (no foreign keys between them)
    "employees": "employees.csv",
    "transactions": "transactions.csv",
    "subscriptions": "subscriptions.csv",
    "usage_log": "usage_log.csv",
    "plan_changes": "plan_changes.csv",
    "tasks": "tasks.csv",
}


def load_db(cfg: Config | None = None) -> dict[str, int]:
    """Load every CSV into its table and return row counts."""
    cfg = cfg or load_config()
    folder = cfg.path("generated")
    missing = [f for f in TABLES.values() if not (folder / f).exists()]
    if missing:
        raise FileNotFoundError(f"Missing {missing} in {folder}. Run `make data` first.")
    conn = connect(cfg.path("sqlite"))
    counts = {}
    with conn:
        for table, file in TABLES.items():
            df = pd.read_csv(folder / file, keep_default_na=False)
            conn.execute(f"DELETE FROM {table}")
            df.to_sql(table, conn, if_exists="append", index=False)
            counts[table] = len(df)
        # Demo logins: every synthetic employee gets the configured demo password (bcrypt-hashed).
        conn.execute("DELETE FROM credentials")
        conn.executemany("INSERT INTO credentials VALUES (?, ?)",
                         [(e, hash_password(cfg.auth.demo_password, cfg.auth.bcrypt_rounds))
                          for e in pd.read_csv(folder / "employees.csv")["employee_id"]])
    conn.close()
    return counts


def main() -> int:
    """CLI entry point."""
    try:
        counts = load_db()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Loaded:", ", ".join(f"{t}={n}" for t, n in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
