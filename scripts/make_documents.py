"""Build the company document corpus (md, docx, pdf) from markdown sources.

Run: python -m scripts.make_documents
Reads data/documents_src/*.md (YAML front matter incl. `format: md|docx|pdf`) and writes
data/documents/<stem>.<format>. Every output starts with the same metadata (title, version,
effective date, status, owner): YAML front matter in .md, "Key: value" lines in .docx/.pdf.
Output is deterministic (fixed timestamps) so the corpus can be committed and re-built.
"""

from __future__ import annotations

import re
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

import yaml
from docx import Document
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

from nexora.config import PROJECT_ROOT

SRC_DIR = PROJECT_ROOT / "data" / "documents_src"
OUT_DIR = PROJECT_ROOT / "data" / "documents"
META_LABELS = (("title", "Title"), ("version", "Version"), ("effective_date", "Effective date"),
               ("status", "Status"), ("owner", "Owner"))
FIXED_TIME = datetime(2026, 1, 1)
INLINE = re.compile(r"(\*\*.+?\*\*|\*.+?\*|`.+?`)")
BULLET = re.compile(r"^- (.*)")
NUMBERED = re.compile(r"^(\d+)\. (.*)")


def parse_source(path: Path) -> tuple[dict[str, Any], str]:
    """Split a source file into front matter (dict of strings) and markdown body."""
    text = path.read_text(encoding="utf-8")
    _, front, body = text.split("---", 2)
    meta = {k: str(v) for k, v in yaml.safe_load(front).items()}
    return meta, body.strip() + "\n"


def blocks(body: str) -> list[tuple[str, str]]:
    """Markdown body -> [(kind, text)] with kinds h1, h2, bullet, numbered, para."""
    out: list[tuple[str, str]] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            out.append(("para", " ".join(paragraph)))
            paragraph.clear()

    for line in body.splitlines():
        if not line.strip():
            flush()
        elif line.startswith("## "):
            flush()
            out.append(("h2", line[3:].strip()))
        elif line.startswith("# "):
            flush()
            out.append(("h1", line[2:].strip()))
        elif m := BULLET.match(line):
            flush()
            out.append(("bullet", m.group(1)))
        elif m := NUMBERED.match(line):
            flush()
            out.append(("numbered", f"{m.group(1)}. {m.group(2)}"))
        else:
            paragraph.append(line.strip())
    flush()
    return out


def metadata_lines(meta: dict[str, str]) -> list[str]:
    """The ``Key: value`` lines placed at the top of docx and pdf files."""
    return [f"{label}: {meta[key]}" for key, label in META_LABELS]


def write_md(meta: dict[str, str], body: str, out: Path) -> None:
    """Markdown output: front matter (without the build-only `format` key) plus body."""
    front = yaml.safe_dump({k: meta[k] for k, _ in META_LABELS}, sort_keys=False, allow_unicode=True)
    out.write_text(f"---\n{front}---\n\n{body}", encoding="utf-8", newline="\n")


def add_runs(paragraph: Any, text: str) -> None:
    """Add text to a python-docx paragraph, honouring **bold**, *italic* and `code`."""
    for part in INLINE.split(text):
        if part.startswith("**") and part.endswith("**"):
            paragraph.add_run(part[2:-2]).bold = True
        elif part.startswith("`") and part.endswith("`"):
            paragraph.add_run(part[1:-1]).font.name = "Courier New"
        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
            paragraph.add_run(part[1:-1]).italic = True
        elif part:
            paragraph.add_run(part)


def write_docx(meta: dict[str, str], body: str, out: Path) -> None:
    """Word output with metadata lines, headings, paragraphs and lists."""
    doc = Document()
    doc.core_properties.created = doc.core_properties.modified = FIXED_TIME
    doc.core_properties.author = "Nexora"
    doc.core_properties.title = meta["title"]
    for line in metadata_lines(meta):
        doc.add_paragraph(line)
    styles = {"h1": ("Heading 1", None), "h2": ("Heading 2", None), "bullet": ("List Bullet", None),
              "numbered": ("Normal", None), "para": ("Normal", None)}
    for kind, text in blocks(body):
        paragraph = doc.add_paragraph(style=styles[kind][0])
        add_runs(paragraph, text)
    doc.save(out)


def pdf_markup(text: str) -> str:
    """Escape text for reportlab and convert inline markdown to its mini-HTML."""
    text = escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"\*(.+?)\*", r"<i>\1</i>", text)
    return re.sub(r"`(.+?)`", r'<font name="Courier">\1</font>', text)


def write_pdf(meta: dict[str, str], body: str, out: Path) -> None:
    """PDF output with metadata lines, headings, paragraphs and lists (selectable text)."""
    base = getSampleStyleSheet()
    styles = {
        "h1": base["Heading1"], "h2": base["Heading2"], "para": base["BodyText"],
        "bullet": ParagraphStyle("bullet", parent=base["BodyText"], leftIndent=18, bulletIndent=6),
        "numbered": ParagraphStyle("numbered", parent=base["BodyText"], leftIndent=18),
        "meta": ParagraphStyle("meta", parent=base["BodyText"], fontSize=9, spaceAfter=0),
    }
    flow: list[Any] = [Paragraph(escape(line), styles["meta"]) for line in metadata_lines(meta)]
    flow.append(Spacer(1, 0.5 * cm))
    for kind, text in blocks(body):
        if kind == "bullet":
            flow.append(Paragraph(pdf_markup(text), styles["bullet"], bulletText="•"))
        else:
            flow.append(Paragraph(pdf_markup(text), styles[kind]))
    SimpleDocTemplate(str(out), pagesize=A4, title=meta["title"], author="Nexora",
                      invariant=True).build(flow)


WRITERS = {"md": write_md, "docx": write_docx, "pdf": write_pdf}


def build(src_dir: Path = SRC_DIR, out_dir: Path = OUT_DIR) -> list[Path]:
    """Rebuild every document in ``out_dir`` from ``src_dir``; return the files written."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.iterdir():
        if old.suffix in WRITERS_SUFFIXES:
            old.unlink()
    written = []
    for src in sorted(src_dir.glob("*.md")):
        meta, body = parse_source(src)
        # The body repeats the title as H1; docx/pdf already carry it, keep it in all formats.
        out = out_dir / f"{src.stem}.{meta['format']}"
        WRITERS[meta["format"]](meta, body, out)
        written.append(out)
    return written


WRITERS_SUFFIXES = tuple(f".{ext}" for ext in WRITERS)


def main() -> None:
    """CLI entry point."""
    files = build()
    print(f"Wrote {len(files)} documents to {OUT_DIR}")
    for f in files:
        print(" ", f.name)


if __name__ == "__main__":
    main()
