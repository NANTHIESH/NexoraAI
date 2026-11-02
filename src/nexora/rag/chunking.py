"""Section-aware chunking with overlap.

Each heading section becomes one chunk. Only sections longer than ``max_tokens`` are split
(on line/sentence boundaries, with ``overlap_tokens`` of overlap) so a chunk never mixes
two sections and the citation [Document title, section] stays exact. Sections shorter than
``min_tokens`` are kept whole rather than merged for the same reason; the window only
applies to the pieces of long sections. Tokens are approximated by whitespace words.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nexora.rag.loaders import LoadedDocument

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    """One retrievable passage with the metadata needed for citations and filtering."""

    chunk_id: str
    source: str
    doc_title: str
    version: str
    effective_date: str
    status: str
    owner: str
    section: str
    page: int | None
    text: str

    @property
    def embedding_text(self) -> str:
        """Text that is embedded: the passage prefixed with its title and section."""
        return f"{self.doc_title} - {self.section}\n{self.text}"

    @property
    def citation(self) -> str:
        """Citation in the format required by the generator prompt."""
        return f"[{self.doc_title}, {self.section}]"


def count_tokens(text: str) -> int:
    """Approximate token count (whitespace words)."""
    return len(text.split())


def split_text(text: str, min_tokens: int, max_tokens: int, overlap_tokens: int) -> list[str]:
    """Split ``text`` into pieces of at most ``max_tokens`` with overlap, on sentence/line breaks."""
    if count_tokens(text) <= max_tokens:
        return [text]
    units = [u for line in text.splitlines() for u in SENTENCE_END.split(line) if u.strip()]
    pieces: list[list[str]] = []
    current: list[str] = []
    for unit in units:
        if current and count_tokens(" ".join(current + [unit])) > max_tokens:
            pieces.append(current)
            tail: list[str] = []
            for prev in reversed(current):  # carry a short tail as overlap
                if count_tokens(" ".join([prev, *tail])) > overlap_tokens:
                    break
                tail.insert(0, prev)
            current = tail
        current.append(unit)
    if current:
        last = " ".join(current)
        if pieces and count_tokens(last) < min_tokens // 4:  # avoid a tiny orphan tail
            pieces[-1].extend(u for u in current if u not in pieces[-1])
        else:
            pieces.append(current)
    return ["\n".join(p) for p in pieces]


def chunk_document(doc: LoadedDocument, min_tokens: int, max_tokens: int,
                   overlap_tokens: int) -> list[Chunk]:
    """Turn a loaded document into chunks (one per section, long sections split)."""
    chunks: list[Chunk] = []
    for section in doc.sections:
        for piece in split_text(section.text, min_tokens, max_tokens, overlap_tokens):
            chunks.append(Chunk(
                chunk_id=f"{doc.source}::{len(chunks):03d}", source=doc.source,
                doc_title=doc.title, version=doc.version, effective_date=doc.effective_date,
                status=doc.status, owner=doc.owner, section=section.heading,
                page=section.page, text=piece))
    return chunks
