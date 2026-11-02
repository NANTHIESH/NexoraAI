"""Sentence-transformers embeddings (all-MiniLM-L6-v2), loaded lazily and cached."""

from __future__ import annotations

import os
from functools import lru_cache

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


@lru_cache(maxsize=2)
def _model(name: str):  # noqa: ANN202 - SentenceTransformer is imported lazily (slow import)
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name)


def embed(texts: list[str], model_name: str) -> list[list[float]]:
    """L2-normalised embeddings, so cosine similarity equals the dot product."""
    vectors = _model(model_name).encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]
