-- Nexora SQLite schema. Applied idempotently by nexora.db.session.connect().
-- Phase 3 creates `documents`; Phase 4 appends the remaining tables.

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
