"""Load md, docx and pdf company documents into a common structure.

Every document starts with the same five metadata fields (see scripts/make_documents.py):
YAML front matter in markdown, "Key: value" lines in docx and pdf. Sections are split on
headings: `##` in markdown, Heading 2 in docx, 14 pt bold text in pdf.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml
from docx import Document
from pypdf import PdfReader

META_LABELS = {"Title": "title", "Version": "version", "Effective date": "effective_date",
               "Status": "status", "Owner": "owner"}
PDF_HEADING_SIZE = 13.0  # reportlab Heading2 is 14 pt, Heading1 18 pt, body 10 pt
PDF_TITLE_SIZE = 16.0
SUPPORTED = (".md", ".docx", ".pdf")
NUMBERED_ITEM = re.compile(r"\d+\. ")
BULLET_GLYPHS = {"\x7f", "•"}


@dataclass(frozen=True)
class Section:
    """A heading and its text; ``page`` is only known for pdf."""

    heading: str
    text: str
    page: int | None = None


@dataclass(frozen=True)
class LoadedDocument:
    """A parsed company document."""

    source: str
    title: str
    version: str
    effective_date: str
    status: str
    owner: str
    sections: list[Section]


class UnsupportedDocumentError(ValueError):
    """Raised for files that are not md, docx or pdf."""


def _split_sections(lines: list[tuple[str, str, int | None]]) -> list[Section]:
    """Group (kind, text, page) lines into sections; kind is 'h' for headings."""
    sections: list[Section] = []
    heading, page, body = "Overview", None, []

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            sections.append(Section(heading, text, page))
        body.clear()

    for kind, text, line_page in lines:
        if kind == "h":
            flush()
            heading, page = text, line_page
        else:
            if not body and heading == "Overview":
                page = line_page
            body.append(text)
    flush()
    return sections


def _load_md(path: Path) -> tuple[dict[str, str], list[Section]]:
    _, front, body = path.read_text(encoding="utf-8").split("---", 2)
    meta = {k: str(v) for k, v in yaml.safe_load(front).items()}
    lines = []
    for raw in body.splitlines():
        if raw.startswith("# "):
            continue
        if raw.startswith("## "):
            lines.append(("h", raw[3:].strip(), None))
        elif raw.strip():
            lines.append(("p", raw.rstrip(), None))
    return meta, _split_sections(lines)


def _load_docx(path: Path) -> tuple[dict[str, str], list[Section]]:
    paragraphs = Document(path).paragraphs
    meta = _parse_meta_lines([p.text for p in paragraphs[: len(META_LABELS)]], path)
    lines = []
    for para in paragraphs[len(META_LABELS):]:
        style, text = para.style.name, para.text.strip()
        if not text or style == "Heading 1":
            continue
        if style == "Heading 2":
            lines.append(("h", text, None))
        else:
            lines.append(("p", f"- {text}" if style == "List Bullet" else text, None))
    return meta, _split_sections(lines)


def _load_pdf(path: Path) -> tuple[dict[str, str], list[Section]]:
    pieces: list[tuple[float, str, int]] = []
    for number, page in enumerate(PdfReader(path).pages, start=1):
        def visit(text: str, _cm, _tm, _font, size: float, page_no: int = number) -> None:
            if text:
                pieces.append((size, text, page_no))

        page.extract_text(visitor_text=visit)
    raw_lines: list[tuple[float, str, int]] = []
    buffer, pending_bullet = "", False
    for size, text, page_no in pieces:
        if text in BULLET_GLYPHS:
            pending_bullet = True
            continue
        buffer += text
        if buffer.endswith("\n"):
            line = buffer.strip()
            raw_lines.append((size, ("- " + line) if pending_bullet else line, page_no))
            buffer, pending_bullet = "", False
    meta = _parse_meta_lines([t for _, t, _ in raw_lines[: len(META_LABELS)]], path)
    lines: list[tuple[str, str, int | None]] = []
    for size, text, page_no in raw_lines[len(META_LABELS):]:
        if size >= PDF_TITLE_SIZE or not text:
            continue
        if size >= PDF_HEADING_SIZE:
            lines.append(("h", text, page_no))
        elif text.startswith("- ") or NUMBERED_ITEM.match(text) or not lines or lines[-1][0] == "h":
            lines.append(("p", text, page_no))
        else:  # continuation of a wrapped line
            kind, prev, prev_page = lines[-1]
            lines[-1] = (kind, f"{prev} {text}", prev_page)
    return meta, _split_sections(lines)


def _parse_meta_lines(lines: list[str], path: Path) -> dict[str, str]:
    meta: dict[str, str] = {}
    for line in lines:
        label, _, value = line.partition(": ")
        if label in META_LABELS:
            meta[META_LABELS[label]] = value.strip()
    if set(meta) != set(META_LABELS.values()):
        raise ValueError(f"{path.name}: missing metadata fields, found {sorted(meta)}")
    return meta


def load_document(path: Path) -> LoadedDocument:
    """Parse one md, docx or pdf file into a :class:`LoadedDocument`."""
    loaders = {".md": _load_md, ".docx": _load_docx, ".pdf": _load_pdf}
    if path.suffix not in loaders:
        raise UnsupportedDocumentError(f"{path.name}: unsupported format (use {SUPPORTED})")
    meta, sections = loaders[path.suffix](path)
    return LoadedDocument(source=path.name, sections=sections, **meta)
