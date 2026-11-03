"""Answer generation with citations, refusal and extractive fallback.

CLI: python -m nexora.rag.generator "How many vacation days do I get?" [--no-docs] [--history]
"""

from __future__ import annotations

import argparse
import re
from dataclasses import dataclass, field
from datetime import date

from nexora.config import Config, load_config
from nexora.llm.fallback import FALLBACK_NOTICE, extractive_answer
from nexora.llm.ollama_client import ModelNotPulledError, OllamaClient, OllamaUnavailableError
from nexora.rag.prompts import BASELINE_SYSTEM_PROMPT, NOT_FOUND, SYSTEM_PROMPT, build_user_prompt
from nexora.rag.retriever import Retriever
from nexora.rag.vectorstore import Hit
from nexora.security.injection import sanitize_hits

CITATION = re.compile(r"\[([^\[\]]+)\]")


@dataclass(frozen=True)
class Answer:
    """Result of answering one question.

    ``mode`` is ``llm``, ``extractive`` (no Ollama) or ``baseline`` (no documents).
    ``citations`` are bracketed citations in the model text that name a retrieved document;
    ``sources`` are all retrieved chunks, shown to the user as expandable evidence.
    """

    text: str
    refused: bool
    mode: str
    citations: list[str] = field(default_factory=list)
    cited_sources: list[str] = field(default_factory=list)
    sources: list[Hit] = field(default_factory=list)
    top_score: float = 0.0
    warning: str | None = None
    notice: str | None = None
    injection_flagged: list[str] = field(default_factory=list)


def parse_citations(text: str, hits: list[Hit]) -> tuple[list[str], list[str]]:
    """Bracketed citations that start with a retrieved document title, plus those files."""
    citations, files = [], []
    for raw in CITATION.findall(text):
        for hit in hits:
            if raw.lower().startswith(hit.chunk.doc_title.lower()):
                citations.append(f"[{raw}]")
                if hit.chunk.source not in files:
                    files.append(hit.chunk.source)
                break
    return citations, files


def outdated_warning(hits: list[Hit], cited_sources: list[str], max_age_days: int, today: date) -> str | None:
    """Warn when the document an answer relies on is older than ``max_age_days``.

    Uses the cited documents, or the best hit when the answer cites none.
    """
    used = [h for h in hits if h.chunk.source in cited_sources] or hits[:1]
    seen, notes = set(), []
    for hit in used:
        c = hit.chunk
        age = (today - date.fromisoformat(c.effective_date)).days
        if c.source not in seen and age > max_age_days:
            seen.add(c.source)
            notes.append(f"'{c.doc_title}' is dated {c.effective_date} ({age // 30} months ago). "
                         "Please check that it is still current.")
    return " ".join(notes) or None


def combine_warnings(*warnings: str | None) -> str | None:
    """Join non-empty warnings into one text."""
    return " ".join(w for w in warnings if w) or None


class Assistant:
    """Document question answering: retrieve, generate, refuse when unsupported."""

    def __init__(self, cfg: Config | None = None, retriever: Retriever | None = None,
                 llm: OllamaClient | None = None, today: date | None = None) -> None:
        self.today = today or date.today()
        self.cfg = cfg or load_config()
        self.retriever = retriever or Retriever(self.cfg)
        self.llm = llm or OllamaClient(self.cfg)
        if self.retriever.llm is None:
            self.retriever.llm = self.llm  # lets the retriever reword questions when query expansion is on

    def _refusal(self, mode: str, hits: list[Hit], top: float, warning: str | None) -> Answer:
        return Answer(self.cfg.refusal_text, True, mode, sources=hits, top_score=top, warning=warning)

    def ask(self, question: str, include_superseded: bool | None = None) -> Answer:
        """Answer from the company documents, or refuse."""
        found = self.retriever.retrieve(question, include_superseded)
        if not found.hits or found.top_score < self.cfg.retrieval.min_retrieval_score:
            return self._refusal("llm", found.hits, found.top_score, found.warning)
        hits, flagged = sanitize_hits(found.hits)  # documents are data, never instructions
        try:
            text = self.llm.chat(SYSTEM_PROMPT, build_user_prompt(question, hits))
        except (OllamaUnavailableError, ModelNotPulledError) as exc:
            best = hits[0]
            return Answer(extractive_answer(hits), False, "extractive",
                          citations=[best.chunk.citation], cited_sources=[best.chunk.source],
                          sources=hits, top_score=found.top_score,
                          warning=combine_warnings(found.warning, self._outdated(hits, [best.chunk.source])),
                          notice=f"{FALLBACK_NOTICE} ({exc})", injection_flagged=flagged)
        if NOT_FOUND in text:
            return self._refusal("llm", hits, found.top_score, found.warning)
        citations, files = parse_citations(text, hits)
        return Answer(text, False, "llm", citations, files, hits, found.top_score,
                      combine_warnings(found.warning, self._outdated(hits, files)), injection_flagged=flagged)

    def _outdated(self, hits: list[Hit], cited_sources: list[str]) -> str | None:
        return outdated_warning(hits, cited_sources, self.cfg.retrieval.outdated_after_days, self.today)

    def ask_without_documents(self, question: str) -> Answer:
        """Baseline: same model and question, no retrieved context (needs Ollama)."""
        text = self.llm.chat(BASELINE_SYSTEM_PROMPT, question)
        return Answer(text, False, "baseline")


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--no-docs", action="store_true", help="baseline: no retrieved context")
    parser.add_argument("--history", action="store_true", help="include superseded documents")
    args = parser.parse_args()
    assistant = Assistant()
    if args.no_docs:
        answer = assistant.ask_without_documents(args.question)
    else:
        answer = assistant.ask(args.question, True if args.history else None)
    print(answer.text)
    if answer.notice:
        print(f"\nNOTICE: {answer.notice}")
    if answer.warning:
        print(f"\nWARNING: {answer.warning}")
    for hit in answer.sources:
        print(f"  - {hit.score:.3f} {hit.chunk.citation} ({hit.chunk.status}, {hit.chunk.source})")


if __name__ == "__main__":
    main()
