"""Text embeddings: sentence-transformers models (default) or embedding models served by Ollama.

``embeddings.model`` in config.yaml selects the model:
- ``sentence-transformers/all-MiniLM-L6-v2``: loaded lazily from the local cache (default);
- ``ollama:<name>`` (for example ``ollama:bge-m3`` or ``ollama:nomic-embed-text``): computed by the local Ollama
  server, so retrieval needs nothing but Ollama.
Vectors are L2-normalised, so cosine similarity equals the dot product. Some models want a different prefix for
questions and for passages; ``kind`` is ``"document"`` when indexing and ``"query"`` when searching.
"""

from __future__ import annotations

import math
import os
from functools import lru_cache

import httpx

from nexora.config import load_config

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

OLLAMA_PREFIX = "ollama:"
OLLAMA_BATCH = 16
# model name -> (passage prefix, question prefix), as documented by the model authors
PREFIXES = {"nomic-embed-text": ("search_document: ", "search_query: ")}
_http = httpx.Client(timeout=120)


@lru_cache(maxsize=2)
def _model(name: str):  # noqa: ANN202 - SentenceTransformer is imported lazily (slow import)
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(name)


def _normalise(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


def _ollama_embed(texts: list[str], model: str, kind: str) -> list[list[float]]:
    """Embeddings from the local Ollama server (``POST /api/embed``)."""
    document_prefix, query_prefix = next((p for name, p in PREFIXES.items() if model.startswith(name)), ("", ""))
    prefix = query_prefix if kind == "query" else document_prefix
    url = load_config().llm.base_url + "/api/embed"
    vectors: list[list[float]] = []
    for start in range(0, len(texts), OLLAMA_BATCH):
        batch = [prefix + t for t in texts[start:start + OLLAMA_BATCH]]
        resp = _http.post(url, json={"model": model, "input": batch})
        if resp.status_code == 404:
            raise RuntimeError(f"Embedding model '{model}' is not available in Ollama. Run `ollama pull {model}`.")
        resp.raise_for_status()
        vectors.extend(_normalise(v) for v in resp.json()["embeddings"])
    return vectors


def embed(texts: list[str], model_name: str, kind: str = "document") -> list[list[float]]:
    """L2-normalised embeddings of ``texts`` (``kind``: ``"document"`` for passages, ``"query"`` for questions)."""
    if model_name.startswith(OLLAMA_PREFIX):
        return _ollama_embed(texts, model_name[len(OLLAMA_PREFIX):], kind)
    vectors = _model(model_name).encode(texts, normalize_embeddings=True, show_progress_bar=False)
    return [v.tolist() for v in vectors]
