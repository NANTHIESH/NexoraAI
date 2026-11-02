"""Prompt-injection guard: retrieved documents are data, never instructions.

Sentences in retrieved text that look like instructions to the model ("ignore previous
instructions", "you are now ...", "do not cite", ...) are removed before the text reaches the
prompt, and the chunk is reported as flagged. The system prompt additionally tells the model to
ignore instructions inside the sources.
"""

from __future__ import annotations

import re
from dataclasses import replace

from nexora.rag.vectorstore import Hit

INJECTION_PATTERNS = [re.compile(p, re.IGNORECASE) for p in (
    r"ignore (all |any |the )?(previous|prior|above|earlier|preceding|other)\b",
    r"disregard (all |any |the )?(previous|prior|above|earlier|instructions|rules)",
    r"forget (all |everything |your )?(previous|prior|above|instructions|rules)",
    r"(new|updated|override|system) (instructions?|prompt|rules?)\s*:",
    r"\byou are now\b",
    r"\bfrom now on,? (you|answer|always|only)\b",
    r"\b(act|behave|respond) as (if|a|an|the)\b",
    r"\bpretend (to be|you)\b",
    r"\b(reveal|print|show|repeat|output) (me )?(your|the) (system )?(prompt|instructions)\b",
    r"\bdo not (cite|mention|follow|use) (the )?(sources?|context|rules?|instructions?)\b",
    r"\balways (answer|reply|respond|say|output)\b",
    r"<\|?(im_start|im_end|system|assistant)\|?>",
    r"\[/?(inst|system)\]",
)]
SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
LIST_ITEM = re.compile(r"^(- |\d+\. )")
REMOVED = "[removed: instruction-like text]"


def looks_like_injection(sentence: str) -> bool:
    """True if ``sentence`` matches an instruction-like pattern."""
    return any(p.search(sentence) for p in INJECTION_PATTERNS)


def _logical_lines(text: str) -> list[str]:
    """Join soft-wrapped lines so an instruction split over two lines is still one sentence."""
    lines: list[str] = []
    for raw in text.splitlines():
        if lines and raw.strip() and not LIST_ITEM.match(raw.strip()) and lines[-1].strip():
            lines[-1] += " " + raw.strip()
        else:
            lines.append(raw.strip())
    return lines


def sanitize_text(text: str) -> tuple[str, bool]:
    """Remove instruction-like sentences; return (clean text, whether anything was removed)."""
    kept, flagged = [], False
    for line in _logical_lines(text):
        pieces = []
        for sentence in SENTENCE_SPLIT.split(line):
            if looks_like_injection(sentence):
                flagged = True
                pieces.append(REMOVED)
            elif sentence.strip():
                pieces.append(sentence)
        kept.append(" ".join(pieces))
    return "\n".join(kept), flagged


def sanitize_hits(hits: list[Hit]) -> tuple[list[Hit], list[str]]:
    """Sanitise every retrieved chunk; return clean hits and the sources that were flagged."""
    clean, flagged_sources = [], []
    for hit in hits:
        text, flagged = sanitize_text(hit.chunk.text)
        if flagged:
            flagged_sources.append(hit.chunk.source)
        clean.append(replace(hit, chunk=replace(hit.chunk, text=text)) if flagged else hit)
    return clean, flagged_sources
