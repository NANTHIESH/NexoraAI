"""Knowledge agent: answers rule questions from the company documents (RAG)."""

from __future__ import annotations

from nexora.rag.generator import Answer, Assistant


class KnowledgeAgent:
    """Thin wrapper so every agent exposes ``run(question)``."""

    def __init__(self, assistant: Assistant | None = None) -> None:
        self.assistant = assistant or Assistant()

    def run(self, question: str, include_superseded: bool | None = None) -> Answer:
        """Cited answer, refusal or extractive fallback."""
        return self.assistant.ask(question, include_superseded)
