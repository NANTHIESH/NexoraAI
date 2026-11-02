"""Phase 3 tests: loaders, chunking, indexing, retrieval, superseded handling, refusal, fallback."""

from __future__ import annotations

import copy
import shutil
from pathlib import Path

import pytest
from scripts.build_index import build_index

from nexora.config import Config, load_config
from nexora.llm.ollama_client import OllamaClient, OllamaUnavailableError
from nexora.rag.chunking import Chunk, chunk_document, count_tokens, split_text
from nexora.rag.generator import Assistant, parse_citations
from nexora.rag.loaders import LoadedDocument, Section, UnsupportedDocumentError, load_document
from nexora.rag.retriever import Retriever, asks_for_history
from nexora.rag.vectorstore import EmptyIndexError, Hit, VectorStore

CORPUS = load_config().path("documents")


def tmp_config(tmp_path: Path, documents: Path = CORPUS) -> Config:
    cfg = Config(copy.deepcopy(dict(load_config())))
    cfg["paths"].update(documents=str(documents), chroma=str(tmp_path / "chroma"),
                        sqlite=str(tmp_path / "nexora.sqlite"))
    return cfg


@pytest.fixture(scope="module")
def indexed(tmp_path_factory: pytest.TempPathFactory) -> Config:
    """The full corpus indexed once into a temporary Chroma + SQLite."""
    cfg = tmp_config(tmp_path_factory.mktemp("index"))
    build_index(cfg)
    return cfg


class FakeLLM:
    """Stands in for Ollama; records whether it was called."""

    def __init__(self, reply: str = "") -> None:
        self.reply, self.calls = reply, 0

    def chat(self, system: str, user: str) -> str:
        self.calls += 1
        return self.reply


class DownLLM:
    def chat(self, system: str, user: str) -> str:
        raise OllamaUnavailableError("http://localhost:1")


# ----------------------------------------------------------------- loaders & metadata
@pytest.mark.parametrize(("file", "title", "version", "status"), [
    ("01-engineering-standards.md", "Engineering Standards", "3", "current"),
    ("06-leave-policy.docx", "Leave Policy: Vacation and Sick Leave", "3", "current"),
    ("08-remote-work-policy-v1.pdf", "Remote Work Policy", "1", "superseded"),
])
def test_loaders_read_metadata_in_every_format(file: str, title: str, version: str, status: str) -> None:
    doc = load_document(CORPUS / file)
    assert (doc.title, doc.version, doc.status) == (title, version, status)
    assert doc.effective_date and doc.owner and doc.sections


def test_pdf_sections_are_split_on_headings_with_page_numbers() -> None:
    doc = load_document(CORPUS / "03-incident-response.pdf")
    assert [s.heading for s in doc.sections] == [
        "Overview", "Severity Levels", "On-Call", "During an Incident", "After an Incident"]
    assert all(s.page == 1 for s in doc.sections)
    assert "acknowledged within 15 minutes" in doc.sections[1].text


def test_unsupported_format_is_rejected(tmp_path: Path) -> None:
    bad = tmp_path / "notes.txt"
    bad.write_text("x")
    with pytest.raises(UnsupportedDocumentError):
        load_document(bad)


# ----------------------------------------------------------------- chunking
def make_doc(text: str) -> LoadedDocument:
    return LoadedDocument("t.md", "Title", "2", "2026-01-01", "current", "Owner",
                          [Section("Intro", "short one."), Section("Long", text)])


def test_chunks_follow_sections_and_keep_metadata() -> None:
    doc = load_document(CORPUS / "06-leave-policy.docx")
    chunks = chunk_document(doc, 300, 500, 50)
    assert [c.section for c in chunks] == ["Vacation", "Sick Leave"]
    first = chunks[0]
    assert (first.doc_title, first.version, first.status, first.source) == (
        "Leave Policy: Vacation and Sick Leave", "3", "current", "06-leave-policy.docx")
    assert first.effective_date == "2026-01-01" and first.owner == "Operations Manager"
    assert first.citation == "[Leave Policy: Vacation and Sick Leave, Vacation]"
    assert first.embedding_text.startswith("Leave Policy: Vacation and Sick Leave - Vacation")


def test_long_sections_are_split_with_overlap_and_size_limit() -> None:
    sentences = [f"Sentence number {i} says something about topic {i % 7}." for i in range(120)]
    chunks = chunk_document(make_doc(" ".join(sentences)), 300, 100, 20)
    long = [c for c in chunks if c.section == "Long"]
    assert len(long) > 1
    assert all(count_tokens(c.text) <= 100 for c in long)
    tail = long[0].text.splitlines()[-1]
    assert tail in long[1].text  # overlap: last sentence repeats at the start of the next chunk
    assert len({c.chunk_id for c in chunks}) == len(chunks)


def test_short_text_is_not_split() -> None:
    assert split_text("one two three", 300, 500, 50) == ["one two three"]


def test_whole_corpus_chunks_stay_within_limit() -> None:
    cfg = load_config().retrieval
    for path in sorted(CORPUS.iterdir()):
        if path.suffix in (".md", ".docx", ".pdf"):
            for c in chunk_document(load_document(path), cfg.chunk_tokens_min,
                                    cfg.chunk_tokens_max, cfg.chunk_overlap_tokens):
                assert 0 < count_tokens(c.text) <= cfg.chunk_tokens_max


# ----------------------------------------------------------------- indexing
def test_index_only_reprocesses_changed_files(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    for name in ("06-leave-policy.docx", "10-payroll.docx", "01-engineering-standards.md"):
        shutil.copy(CORPUS / name, docs / name)
    cfg = tmp_config(tmp_path, docs)
    assert build_index(cfg) == {"indexed": 3, "skipped": 0, "removed": 0}
    assert build_index(cfg) == {"indexed": 0, "skipped": 3, "removed": 0}
    md = docs / "01-engineering-standards.md"
    md.write_text(md.read_text(encoding="utf-8") + "\nExtra line.\n", encoding="utf-8")
    assert build_index(cfg) == {"indexed": 1, "skipped": 2, "removed": 0}
    (docs / "10-payroll.docx").unlink()
    assert build_index(cfg) == {"indexed": 0, "skipped": 2, "removed": 1}
    assert VectorStore(Path(cfg["paths"]["chroma"]), cfg.retrieval.collection).count() > 0


def test_empty_index_gives_clear_error(tmp_path: Path) -> None:
    store = VectorStore(tmp_path / "empty", "empty_test")
    with pytest.raises(EmptyIndexError, match="make index"):
        store.search([0.0] * 384, 5, False)


def test_missing_documents_folder_names_the_make_target(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="make docs"):
        build_index(tmp_config(tmp_path, tmp_path / "nothing"))


# ----------------------------------------------------------------- retrieval
def test_retrieval_finds_the_right_document(indexed: Config) -> None:
    res = Retriever(indexed).retrieve("How many vacation days do full-time employees get?")
    assert res.hits[0].chunk.source == "06-leave-policy.docx"
    assert len(res.hits) == 5 and res.top_score > 0.5


def test_superseded_documents_are_excluded_by_default(indexed: Config) -> None:
    res = Retriever(indexed).retrieve("How many days per week can I work remotely?")
    assert res.hits and all(h.chunk.status == "current" for h in res.hits)
    assert res.hits[0].chunk.source == "07-remote-work-policy-v2.md"
    assert res.warning is None


def test_superseded_documents_are_included_for_history_questions_with_warning(indexed: Config) -> None:
    question = "What was the previous remote work rule?"
    assert asks_for_history(question) and not asks_for_history("How many vacation days?")
    res = Retriever(indexed).retrieve(question)
    assert any(h.chunk.status == "superseded" for h in res.hits)
    assert res.warning and "superseded" in res.warning


# ----------------------------------------------------------------- generation, refusal, fallback
def test_unrelated_question_is_refused_without_calling_the_model(indexed: Config) -> None:
    llm = FakeLLM("should never be used")
    answer = Assistant(indexed, Retriever(indexed), llm).ask("What is the capital of France?")
    assert answer.refused and answer.text == indexed.refusal_text
    assert llm.calls == 0 and answer.top_score < indexed.retrieval.min_retrieval_score


def test_not_found_from_the_model_becomes_a_refusal(indexed: Config) -> None:
    llm = FakeLLM("NOT_FOUND")
    answer = Assistant(indexed, Retriever(indexed), llm).ask("How many vacation days do I get?")
    assert answer.refused and answer.text == indexed.refusal_text and llm.calls == 1


def test_answer_keeps_valid_citations_only(indexed: Config) -> None:
    reply = ("Full-time employees get 25 days. [Leave Policy: Vacation and Sick Leave, Vacation] "
             "[Made Up Handbook, Chapter 1]")
    answer = Assistant(indexed, Retriever(indexed), FakeLLM(reply)).ask(
        "How many vacation days do full-time employees get?")
    assert not answer.refused and answer.mode == "llm"
    assert answer.citations == ["[Leave Policy: Vacation and Sick Leave, Vacation]"]
    assert answer.cited_sources == ["06-leave-policy.docx"]


def test_parse_citations_ignores_unknown_documents() -> None:
    chunk = Chunk("a::000", "a.md", "Payroll", "1", "2026-01-01", "current", "O", "Pay", None, "t")
    assert parse_citations("x [Payroll, Pay] [Other, Thing]", [Hit(chunk, 0.9)]) == (
        ["[Payroll, Pay]"], ["a.md"])


def test_extractive_fallback_when_ollama_is_down(indexed: Config) -> None:
    answer = Assistant(indexed, Retriever(indexed), DownLLM()).ask(
        "How many vacation days do full-time employees get?")
    assert answer.mode == "extractive" and not answer.refused
    assert "25 days of paid vacation" in answer.text
    assert answer.citations == ["[Leave Policy: Vacation and Sick Leave, Vacation]"]
    assert answer.notice and "extractive mode" in answer.notice


def test_unavailable_ollama_error_message_is_actionable() -> None:
    cfg = Config(copy.deepcopy(dict(load_config())))
    cfg["llm"]["base_url"] = "http://127.0.0.1:1"
    client = OllamaClient(cfg)
    assert client.is_ready() is False
    with pytest.raises(OllamaUnavailableError, match="ollama.com"):
        client.chat("s", "u")


@pytest.mark.skipif(not OllamaClient().is_ready(), reason="Ollama or the model is not available")
def test_real_model_answers_and_refuses(indexed: Config) -> None:
    assistant = Assistant(indexed, Retriever(indexed))
    ok = assistant.ask("How many vacation days do full-time employees get?")
    assert "25" in ok.text and ok.cited_sources == ["06-leave-policy.docx"]
    assert assistant.ask("What is the capital of France?").refused


# ----------------------------------------------------------------- Phase 6: hybrid search and reranker
def retrieval_config(indexed: Config, hybrid: bool, rerank: bool) -> Config:
    cfg = Config(copy.deepcopy(dict(indexed)))
    cfg["retrieval"]["hybrid"] = hybrid
    cfg["reranker"]["enabled"] = rerank
    return cfg


def test_reciprocal_rank_fusion_prefers_items_ranked_high_in_both_lists() -> None:
    from nexora.rag.retriever import reciprocal_rank_fusion

    fused = reciprocal_rank_fusion([["a", "b", "c"], ["c", "b", "d"]], k=60)
    assert set(fused[:2]) == {"b", "c"} and set(fused) == {"a", "b", "c", "d"}


def test_tokenizer_drops_stopwords_and_keeps_rare_terms() -> None:
    from nexora.rag.retriever import tokenize

    assert tokenize("What does SSPL mean for our client work?") == ["sspl", "mean", "client", "work"]


def test_hybrid_search_finds_an_exact_term_the_embedding_may_miss(indexed: Config) -> None:
    res = Retriever(retrieval_config(indexed, hybrid=True, rerank=False)).retrieve("SSPL")
    assert "01-engineering-standards.md" in [h.chunk.source for h in res.hits]
    assert all(0.0 <= h.score <= 1.0 for h in res.hits) and res.top_score > 0


def test_hybrid_keeps_superseded_documents_out_by_default(indexed: Config) -> None:
    res = Retriever(retrieval_config(indexed, hybrid=True, rerank=False)).retrieve("remote work core hours")
    assert res.hits and all(h.chunk.status == "current" for h in res.hits)


class ReverseReranker:
    """Fake cross-encoder that prefers the chunk about sick leave."""

    def score(self, question: str, chunks: list[Chunk]) -> list[float]:
        return [10.0 if c.section == "Sick Leave" else 0.0 for c in chunks]


def test_reranker_reorders_candidates_and_records_its_score(indexed: Config) -> None:
    cfg = retrieval_config(indexed, hybrid=False, rerank=True)
    res = Retriever(cfg, reranker=ReverseReranker()).retrieve("How many vacation days do I get?")
    assert res.hits[0].chunk.section == "Sick Leave" and res.hits[0].rerank_score == 10.0
    assert len(res.hits) == cfg.retrieval.top_k


def test_refusal_threshold_uses_the_best_embedding_score_in_every_mode(indexed: Config) -> None:
    for hybrid, rerank in [(False, False), (True, False), (True, True)]:
        retriever = Retriever(retrieval_config(indexed, hybrid, rerank))
        unrelated = retriever.retrieve("What is the capital of France?")
        assert unrelated.top_score < indexed.retrieval.min_retrieval_score
        assert retriever.retrieve("How many vacation days do full-time employees get?").top_score > 0.6


def test_real_reranker_puts_the_right_document_first(indexed: Config) -> None:
    retriever = Retriever(retrieval_config(indexed, hybrid=True, rerank=True))
    res = retriever.retrieve("Who signs off on purchases over a hundred euros?")
    assert res.hits[0].chunk.source == "09-expenses-equipment-training.docx"
    assert res.hits[0].rerank_score is not None
