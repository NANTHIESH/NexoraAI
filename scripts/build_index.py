"""Index company documents into ChromaDB, re-indexing only files whose content changed.

Run: python -m scripts.build_index [--rebuild]   (or: make index)
File hashes live in the SQLite `documents` table.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from datetime import UTC, datetime
from pathlib import Path

from nexora.config import Config, load_config
from nexora.db.session import connect
from nexora.rag.chunking import chunk_document
from nexora.rag.embeddings import embed
from nexora.rag.loaders import SUPPORTED, load_document
from nexora.rag.vectorstore import VectorStore


def sha256_of(path: Path) -> str:
    """Hex SHA-256 of a file's bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_index(cfg: Config | None = None, rebuild: bool = False,
                store: VectorStore | None = None) -> dict[str, int]:
    """Sync the index with data/documents; returns counts of indexed/skipped/removed files."""
    cfg = cfg or load_config()
    folder = cfg.path("documents")
    files = sorted(p for p in folder.glob("*") if p.suffix in SUPPORTED) if folder.exists() else []
    if not files:
        raise FileNotFoundError(f"No documents found in {folder}. Run `make docs` first.")
    store = store or VectorStore(cfg.path("chroma"), cfg.retrieval.collection)
    db = connect(cfg.path("sqlite"))
    known = {r["path"]: r["sha256"] for r in db.execute("SELECT path, sha256 FROM documents")}
    counts = {"indexed": 0, "skipped": 0, "removed": 0}
    ch = cfg.retrieval
    for path in files:
        digest = sha256_of(path)
        if not rebuild and known.get(path.name) == digest:
            counts["skipped"] += 1
            continue
        doc = load_document(path)
        chunks = chunk_document(doc, ch.chunk_tokens_min, ch.chunk_tokens_max, ch.chunk_overlap_tokens)
        store.delete_source(path.name)
        store.add(chunks, embed([c.embedding_text for c in chunks], cfg.embeddings.model))
        db.execute("INSERT OR REPLACE INTO documents VALUES (?,?,?,?,?,?,?,?,?)",
                   (path.name, digest, doc.title, doc.version, doc.effective_date, doc.status,
                    doc.owner, len(chunks), datetime.now(UTC).isoformat(timespec="seconds")))
        counts["indexed"] += 1
    for gone in set(known) - {p.name for p in files}:
        store.delete_source(gone)
        db.execute("DELETE FROM documents WHERE path = ?", (gone,))
        counts["removed"] += 1
    db.commit()
    db.close()
    return counts


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rebuild", action="store_true", help="re-index every file")
    args = parser.parse_args()
    try:
        counts = build_index(rebuild=args.rebuild)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return 1
    print("Index updated:", ", ".join(f"{k}={v}" for k, v in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
