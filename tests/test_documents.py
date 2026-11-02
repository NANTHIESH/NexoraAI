"""Phase 2 tests: corpus size and formats, metadata, required facts, forbidden topics."""

from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import pytest
import yaml
from docx import Document
from pypdf import PdfReader
from scripts.make_documents import build

from nexora.config import load_config

META_KEYS = ("Title", "Version", "Effective date", "Status", "Owner")


def read_document(path: Path) -> tuple[dict[str, str], str]:
    """Return (metadata, full text) for an md, docx or pdf document."""
    if path.suffix == ".md":
        _, front, body = path.read_text(encoding="utf-8").split("---", 2)
        meta = {k: str(v) for k, v in yaml.safe_load(front).items()}
        return meta, body
    if path.suffix == ".docx":
        lines = [p.text for p in Document(path).paragraphs]
    else:
        lines = "\n".join(p.extract_text() for p in PdfReader(path).pages).splitlines()
    pairs = dict(line.split(": ", 1) for line in lines[: len(META_KEYS)])
    assert tuple(pairs) == META_KEYS, (path.name, pairs)
    meta = {"title": pairs["Title"], "version": pairs["Version"],
            "effective_date": pairs["Effective date"], "status": pairs["Status"],
            "owner": pairs["Owner"]}
    return meta, "\n".join(lines[len(META_KEYS):])


@pytest.fixture(scope="module")
def corpus() -> dict[str, tuple[dict[str, str], str]]:
    folder = load_config().path("documents")
    files = sorted(p for p in folder.iterdir() if p.suffix in (".md", ".docx", ".pdf"))
    return {p.name: read_document(p) for p in files}


def test_corpus_size_and_formats(corpus: dict) -> None:
    assert 18 <= len(corpus) <= 20
    formats = Counter(Path(name).suffix for name in corpus)
    assert all(formats[ext] >= 3 for ext in (".md", ".docx", ".pdf")), formats


def test_every_document_has_complete_metadata(corpus: dict) -> None:
    for name, (meta, text) in corpus.items():
        assert set(meta) == {"title", "version", "effective_date", "status", "owner"}, name
        assert meta["status"] in ("current", "superseded"), name
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", meta["effective_date"]), name
        assert len(text.split()) > 60, name


# (fact, regex, file prefix that must contain it)
FACTS = [
    ("25 vacation days", r"25 days of paid vacation per year", "06-"),
    ("carry over 5 days", r"Up to 5 unused vacation days can be carried over", "06-"),
    ("2 weeks notice", r"at least 2 weeks in advance", "06-"),
    ("remote 3 days", r"up to 3 days per week", "07-"),
    ("core hours", r"10:00 to 16:00", "07-"),
    ("expense approval", r"above 100 EUR need owner approval", "09-"),
    ("receipts 30 days", r"receipts within 30 days", "09-"),
    ("2 approvals", r"needs 2 approvals before merge", "01-"),
    ("friday deploy", r"No production deployments on Fridays after 14:00", "02-"),
    ("training budget", r"1,000 EUR per developer per year", "09-"),
    ("tool approval", r"needs owner approval and must be registered in the subscription list "
                      r"before purchase", "11-"),
    ("sick notify", r"before 09:30 on day one", "06-"),
    ("medical certificate", r"medical certificate is required after 3 consecutive days", "06-"),
    ("customer data", r"never be sent to personal email or stored on personal devices", "05-"),
    ("sev-1", r"Sev-1 incident must be acknowledged within 15 minutes", "03-"),
    ("payslips", r"Payslips are sent on the 28th of each month", "10-"),
]


@pytest.mark.parametrize(("fact", "pattern", "prefix"), FACTS, ids=[f[0] for f in FACTS])
def test_required_fact_present_in_expected_document(
        corpus: dict, fact: str, pattern: str, prefix: str) -> None:
    hits = [name for name, (_, text) in corpus.items()
            if re.search(pattern, " ".join(text.split()), re.IGNORECASE)]
    assert any(h.startswith(prefix) for h in hits), (fact, hits)


def test_refusal_topics_are_absent(corpus: dict) -> None:
    forbidden = re.compile(r"\bbonus|\bdress\b|attire|four[- ]day|4[- ]day", re.IGNORECASE)
    for name, (meta, text) in corpus.items():
        assert not forbidden.search(meta["title"] + " " + text), name


def test_remote_policy_has_superseded_older_version(corpus: dict) -> None:
    remote = {meta["version"]: (meta, text) for meta, text in corpus.values()
              if meta["title"] == "Remote Work Policy"}
    assert set(remote) == {"1", "2"}
    v1, v2 = remote["1"][0], remote["2"][0]
    assert v1["status"] == "superseded" and v2["status"] == "current"
    assert v1["effective_date"] < v2["effective_date"]
    assert "2 days per week" in remote["1"][1] and "3 days per week" in remote["2"][1]


def test_rebuild_matches_committed_corpus(tmp_path: Path, corpus: dict) -> None:
    build(out_dir=tmp_path)
    rebuilt = {p.name: read_document(p) for p in sorted(tmp_path.iterdir())}
    assert rebuilt == corpus
