"""Extractive fallback used when Ollama is not available."""

from __future__ import annotations

from nexora.rag.vectorstore import Hit

FALLBACK_NOTICE = ("Running in extractive mode: the local language model (Ollama) is not available, "
                   "so Nexora shows the best matching passage from the company documents instead "
                   "of writing an answer.")


def extractive_answer(hits: list[Hit]) -> str:
    """Best passage followed by its citation."""
    best = hits[0].chunk
    return f"{best.text}\n\n{best.citation}"
