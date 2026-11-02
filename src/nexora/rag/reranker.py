"""Cross-encoder reranker (ms-marco-MiniLM-L-6-v2), loaded lazily and cached."""

from __future__ import annotations

import os
from functools import lru_cache

from nexora.rag.chunking import Chunk

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@lru_cache(maxsize=1)
def _model(name: str):  # noqa: ANN202 - CrossEncoder is imported lazily (slow import)
    from sentence_transformers import CrossEncoder

    return CrossEncoder(name)


class Reranker:
    """Scores (question, passage) pairs; higher means more relevant (raw logits, not 0-1)."""

    def __init__(self, model_name: str) -> None:
        self.model_name = model_name

    def score(self, question: str, chunks: list[Chunk]) -> list[float]:
        """Relevance score for each chunk, in the same order."""
        if not chunks:
            return []
        pairs = [(question, f"{c.doc_title} - {c.section}: {c.text}") for c in chunks]
        return [float(s) for s in _model(self.model_name).predict(pairs, show_progress_bar=False)]
