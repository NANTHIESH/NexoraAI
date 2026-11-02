"""Read access to the business tables as pandas frames (from SQLite or from the CSV folder)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from nexora.config import Config

TABLES = ("employees", "transactions", "subscriptions", "usage_log", "plan_changes", "tasks")


class MissingDataError(RuntimeError):
    """Raised when the business tables are empty or the CSV files are absent."""

    def __init__(self, what: str) -> None:
        super().__init__(f"{what}. Run `make data` and `make load` first.")


@dataclass
class Frames:
    """All business data used by detectors and agents."""

    employees: pd.DataFrame
    transactions: pd.DataFrame
    subscriptions: pd.DataFrame
    usage_log: pd.DataFrame
    plan_changes: pd.DataFrame
    tasks: pd.DataFrame

    def software_charges(self, cfg: Config) -> pd.DataFrame:
        """Money-out transactions in the configured software categories."""
        tx = self.transactions
        mask = tx["category"].isin(cfg.detectors.software_categories) & (tx["amount"] < 0)
        return tx[mask].copy()


def _prepare(frames: dict[str, pd.DataFrame]) -> Frames:
    tx = frames["transactions"]
    tx["date"] = pd.to_datetime(tx["date"])
    tx["month"] = tx["date"].dt.strftime("%Y-%m")
    tasks = frames["tasks"]
    for col in ("created", "due", "completed"):
        tasks[col] = pd.to_datetime(tasks[col].replace("", pd.NA))
    return Frames(**frames)


def frames_from_db(conn: sqlite3.Connection) -> Frames:
    """Load all business tables from SQLite."""
    frames = {t: pd.read_sql_query(f"SELECT * FROM {t}", conn) for t in TABLES}
    if frames["transactions"].empty:
        raise MissingDataError("The transactions table is empty")
    return _prepare(frames)


def frames_from_csv(folder: Path) -> Frames:
    """Load the generated CSV files directly (used by evaluation and tests)."""
    try:
        frames = {t: pd.read_csv(folder / f"{t}.csv", keep_default_na=False) for t in TABLES}
    except FileNotFoundError as exc:
        raise MissingDataError(f"Missing data file {exc.filename}") from exc
    return _prepare(frames)


def save_report(conn: sqlite3.Connection, period: str, ref_date: str, data: dict,
                summary: str, created_by: str | None = None) -> int:
    """Store a generated report and return its id."""
    with conn:
        cur = conn.execute(
            "INSERT INTO reports (period, ref_date, data_json, summary, created_by, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (period, ref_date, json.dumps(data), summary, created_by,
             datetime.now(UTC).isoformat(timespec="seconds")))
    return int(cur.lastrowid)
