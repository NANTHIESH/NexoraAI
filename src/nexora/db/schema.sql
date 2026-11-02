-- Nexora SQLite schema. Applied idempotently by nexora.db.session.connect().
-- Phase 3 created `documents`; Phase 4 added the business and application tables.

CREATE TABLE IF NOT EXISTS documents (
    path           TEXT PRIMARY KEY,      -- file name inside data/documents
    sha256         TEXT NOT NULL,         -- content hash used to re-index only changed files
    title          TEXT NOT NULL,
    version        TEXT NOT NULL,
    effective_date TEXT NOT NULL,
    status         TEXT NOT NULL,         -- current | superseded
    owner          TEXT NOT NULL,
    n_chunks       INTEGER NOT NULL,
    indexed_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS employees (
    employee_id          TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    email                TEXT NOT NULL,
    role                 TEXT NOT NULL,   -- owner | ops_manager | pm | employee
    job_title            TEXT NOT NULL,
    department           TEXT NOT NULL,
    hire_date            TEXT NOT NULL,
    gross_monthly_salary REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS transactions (
    txn_id       TEXT PRIMARY KEY,
    date         TEXT NOT NULL,
    description  TEXT NOT NULL,
    category     TEXT NOT NULL,
    counterparty TEXT NOT NULL,
    amount       REAL NOT NULL,           -- EUR, negative = money out
    balance      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(date);

CREATE TABLE IF NOT EXISTS subscriptions (
    sub_id        TEXT PRIMARY KEY,
    tool          TEXT NOT NULL UNIQUE,
    category      TEXT NOT NULL,
    plan          TEXT NOT NULL,
    pricing_model TEXT NOT NULL,          -- fixed | usage_based | annual
    monthly_cost  REAL NOT NULL,
    seats         INTEGER NOT NULL,
    billing_day   INTEGER NOT NULL,
    owner         TEXT NOT NULL,
    start_month   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS usage_log (
    tool         TEXT NOT NULL,
    month        TEXT NOT NULL,
    active_users INTEGER NOT NULL,
    logins       INTEGER NOT NULL,
    last_used    TEXT NOT NULL,
    PRIMARY KEY (tool, month)
);

CREATE TABLE IF NOT EXISTS plan_changes (
    change_id        TEXT PRIMARY KEY,
    tool             TEXT NOT NULL,
    month            TEXT NOT NULL,
    change_type      TEXT NOT NULL,       -- plan_change | documented_usage_spike
    old_monthly_cost REAL,
    new_monthly_cost REAL,
    note             TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id      TEXT PRIMARY KEY,
    project      TEXT NOT NULL,
    type         TEXT NOT NULL,
    title        TEXT NOT NULL,
    owner        TEXT NOT NULL,           -- employee_id
    status       TEXT NOT NULL,           -- todo | in_progress | blocked | done
    priority     TEXT NOT NULL,
    story_points INTEGER,
    sprint       TEXT,
    created      TEXT NOT NULL,
    due          TEXT NOT NULL,
    completed    TEXT,
    weekly_note  TEXT
);

CREATE TABLE IF NOT EXISTS chat_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    TEXT NOT NULL,
    session_id TEXT NOT NULL,
    role       TEXT NOT NULL,             -- user | assistant
    content    TEXT NOT NULL,             -- PII-redacted before storage
    intent     TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS feedback (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id    INTEGER REFERENCES chat_history(id),
    user_id    TEXT NOT NULL,
    rating     INTEGER NOT NULL CHECK (rating IN (-1, 1)),
    comment    TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      TEXT NOT NULL,
    user_id TEXT NOT NULL,
    role    TEXT NOT NULL,
    question TEXT,
    intent  TEXT,
    sources TEXT,                          -- JSON list of cited documents
    action  TEXT NOT NULL,
    detail  TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    period     TEXT NOT NULL,              -- weekly | monthly | yearly
    ref_date   TEXT NOT NULL,
    data_json  TEXT NOT NULL,
    summary    TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS eval_results (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_at    TEXT NOT NULL,
    suite     TEXT NOT NULL,               -- rules | detection | time
    name      TEXT NOT NULL,
    metric    TEXT NOT NULL,
    value     REAL,
    detail    TEXT
);
