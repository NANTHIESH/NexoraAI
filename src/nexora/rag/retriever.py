"""Semantic retriever with superseded-document handling."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nexora.config import Config, load_config
from nexora.rag.embeddings import embed
from nexora.rag.vectorstore import Hit, VectorStore

HISTORY_WORDS = re.compile(
    r"\b(previous|previously|earlier|old|older|former|formerly|history|historical|original|"
    r"superseded|outdated|used to|before|changed|change|v1|version 1|last year)\b",
    re.IGNORECASE)
SUPERSEDED_WARNING = ("Some sources are superseded (outdated) document versions, shown because "
                      "you asked about earlier rules. The current version is marked 'current'.")


def asks_for_history(question: str) -> bool:
    """True if the user seems to ask about earlier (superseded) rules."""
    return bool(HISTORY_WORDS.search(question))


@dataclass(frozen=True)
class Retrieval:
    """Search result: hits, whether superseded documents were included, and a warning."""

    hits: list[Hit]
    include_superseded: bool
    warning: str | None

    @property
    def top_score(self) -> float:
        """Best similarity score, 0.0 when nothing was found."""
        return self.hits[0].score if self.hits else 0.0


class Retriever:
    """Top-k semantic search over the indexed company documents."""

    def __init__(self, cfg: Config | None = None, store: VectorStore | None = None) -> None:
        self.cfg = cfg or load_config()
        self.store = store or VectorStore(self.cfg.path("chroma"), self.cfg.retrieval.collection)

    def retrieve(self, question: str, include_superseded: bool | None = None) -> Retrieval:
        """Search; superseded documents are excluded unless the question asks about history."""
        if include_superseded is None:
            include_superseded = asks_for_history(question)
        vector = embed([question], self.cfg.embeddings.model)[0]
        hits = self.store.search(vector, self.cfg.retrieval.top_k, include_superseded)
        has_old = any(h.chunk.status == "superseded" for h in hits)
        return Retrieval(hits, include_superseded, SUPERSEDED_WARNING if has_old else None)
