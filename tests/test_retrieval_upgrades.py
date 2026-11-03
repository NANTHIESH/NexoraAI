"""Ollama embeddings, one index per embedding model, and query expansion in the retriever."""

from __future__ import annotations

import copy
import json
import math

import pytest

from nexora.config import Config, load_config
from nexora.rag import embeddings
from nexora.rag.chunking import Chunk
from nexora.rag.retriever import Retriever
from nexora.rag.vectorstore import Hit, collection_name


def _cfg(**retrieval: object) -> Config:
    cfg = Config(copy.deepcopy(dict(load_config())))
    cfg["retrieval"].update(retrieval)
    cfg["reranker"]["enabled"] = False
    return cfg


# ------------------------------------------------------------------ one index per embedding model
def test_every_embedding_model_has_its_own_collection() -> None:
    default = _cfg()
    assert collection_name(default) == default.retrieval.collection  # existing indexes stay valid
    other = _cfg()
    other["embeddings"]["model"] = "ollama:bge-m3"
    assert collection_name(other) == f"{default.retrieval.collection}__ollama-bge-m3"


# ------------------------------------------------------------------ Ollama embeddings
class _Reply:
    status_code = 200

    def __init__(self, count: int) -> None:
        self._count = count

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"embeddings": [[3.0, 4.0]] * self._count}


class _FakeHttp:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def post(self, url: str, json: dict) -> _Reply:  # noqa: A002 - mirrors httpx
        self.calls.append({"url": url, **json})
        return _Reply(len(json["input"]))


def test_ollama_embeddings_are_normalised_prefixed_and_batched(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeHttp()
    monkeypatch.setattr(embeddings, "_http", fake)
    vectors = embeddings.embed([f"text {i}" for i in range(40)], "ollama:nomic-embed-text", "document")
    assert len(vectors) == 40 and len(fake.calls) == 3  # batches of 16
    assert math.isclose(sum(x * x for x in vectors[0]), 1.0) and vectors[0] == pytest.approx([0.6, 0.8])
    assert fake.calls[0]["url"].endswith("/api/embed") and fake.calls[0]["model"] == "nomic-embed-text"
    assert fake.calls[0]["input"][0] == "search_document: text 0"
    embeddings.embed(["a question"], "ollama:nomic-embed-text", "query")
    assert fake.calls[-1]["input"] == ["search_query: a question"]
    embeddings.embed(["a question"], "ollama:bge-m3", "query")  # this model needs no prefix
    assert fake.calls[-1]["input"] == ["a question"]


def test_a_missing_ollama_embedding_model_says_how_to_get_it(monkeypatch: pytest.MonkeyPatch) -> None:
    class NotFound(_FakeHttp):
        def post(self, url: str, json: dict) -> _Reply:  # noqa: A002
            reply = _Reply(0)
            reply.status_code = 404
            return reply

    monkeypatch.setattr(embeddings, "_http", NotFound())
    with pytest.raises(RuntimeError, match="ollama pull bge-m3"):
        embeddings.embed(["x"], "ollama:bge-m3")


# ------------------------------------------------------------------ query expansion
class _LLM:
    def __init__(self, reply: str | Exception) -> None:
        self.reply, self.calls = reply, 0

    def chat(self, system: str, user: str, json_mode: bool = False) -> str:
        self.calls += 1
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def _retriever(llm: _LLM | None, expansion: int = 2) -> Retriever:
    return Retriever(_cfg(query_expansion=expansion), store=object(), llm=llm)  # type: ignore[arg-type]


def test_expansion_keeps_clean_new_queries_and_caches_them() -> None:
    reply = json.dumps({"queries": ["vacation request notice", "How early do I ask for time off?", "x" * 500, "", 7,
                                    "leave tool approval", "third one is cut"]})
    llm = _LLM(reply)
    retriever = _retriever(llm)
    assert retriever._expand("How early do I ask for time off?") == ["vacation request notice", "leave tool approval"]
    retriever._expand("How early do I ask for time off?")
    assert llm.calls == 1  # the same question is not reworded twice


@pytest.mark.parametrize("reply", ["not json", "[]", '{"queries": "oops"}', RuntimeError("model is down")])
def test_expansion_failure_means_search_without_it(reply: str | Exception) -> None:
    assert _retriever(_LLM(reply))._expand("anything") == []


def test_expansion_is_off_by_default_and_without_a_model() -> None:
    llm = _LLM(json.dumps({"queries": ["a b c"]}))
    assert _retriever(llm, expansion=0)._expand("q") == [] and llm.calls == 0
    assert _retriever(None)._expand("q") == []


class _Store:
    """Two passages in a 2-d space: 'wrong' matches the question, 'right' matches only its reworded version."""

    def __init__(self) -> None:
        make = lambda cid, text: Chunk(cid, "leave.md", "Leave", "1", "2026-01-01", "current", "HR", cid, None, text)  # noqa: E731
        self.vectors = {"z-wrong": [1.0, 0.0], "a-right": [0.0, 1.0]}
        self.chunks = {cid: make(cid, cid) for cid in self.vectors}

    def search(self, embedding: list[float], k: int, include_superseded: bool) -> list[Hit]:
        def similarity(chunk_id: str) -> float:
            return sum(a * b for a, b in zip(embedding, self.vectors[chunk_id], strict=True))

        scored = sorted(self.vectors, key=lambda c: -similarity(c))
        return [Hit(self.chunks[c], similarity(c)) for c in scored[:k]]

    def embeddings_for(self, ids: list[str]) -> dict[str, list[float]]:
        return {i: self.vectors[i] for i in ids}

    def count(self) -> int:
        return len(self.vectors)


def test_a_reworded_query_can_rescue_a_passage_the_question_alone_misses(monkeypatch: pytest.MonkeyPatch) -> None:
    vectors = {"time off?": [1.0, 0.0], "vacation request": [0.0, 1.0]}
    monkeypatch.setattr("nexora.rag.retriever.embed", lambda texts, model, kind="document": [vectors[t] for t in texts])
    reply = json.dumps({"queries": ["vacation request"]})
    base = dict(top_k=1, candidates=1, hybrid=False)
    without = Retriever(_cfg(query_expansion=0, **base), store=_Store(), llm=_LLM(reply)).retrieve("time off?")
    with_expansion = Retriever(_cfg(query_expansion=1, **base), store=_Store(), llm=_LLM(reply)).retrieve("time off?")
    assert [h.chunk.chunk_id for h in without.hits] == ["z-wrong"]
    assert [h.chunk.chunk_id for h in with_expansion.hits] == ["a-right"]
