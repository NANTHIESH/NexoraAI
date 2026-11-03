"""Retriever: semantic search, optional hybrid BM25 fusion, optional cross-encoder reranking.

Modes (config.yaml): ``retrieval.hybrid`` adds BM25 keyword search and fuses both rankings
with reciprocal rank fusion; ``retrieval.query_expansion`` (N > 0) also searches N rewordings of the
question written by the local Ollama model and fuses those rankings too; ``reranker.enabled``
re-orders the candidate pool with a cross-encoder. ``Hit.score`` is always the embedding cosine
similarity to the ORIGINAL question, so the refusal threshold means the same in every mode.
Superseded documents are excluded unless the question asks about earlier rules.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, replace
from typing import Protocol

import numpy as np
from rank_bm25 import BM25Okapi

from nexora.config import Config, load_config
from nexora.rag.chunking import Chunk
from nexora.rag.embeddings import embed
from nexora.rag.reranker import Reranker
from nexora.rag.vectorstore import Hit, VectorStore, collection_name

log = logging.getLogger("nexora.retriever")
MAX_EXPANSION_CHARS = 160
EXPANSION_PROMPT = """You help search a small company's policy documents (leave, expenses, security, deployment, payroll...).
Rewrite the employee's question as {n} different short search queries that use other words: synonyms, formal policy wording, the likely title of the policy. Keep names, numbers and tools exactly. Do not answer the question.
Example question: "How early do I have to ask for time off?"
Example output: {{"queries": ["vacation request notice period in advance", "leave tool request approval manager"]}}
Output JSON only: {{"queries": [...]}}"""


class SupportsChat(Protocol):
    """Anything with the OllamaClient.chat signature."""

    def chat(self, system: str, user: str, json_mode: bool = False) -> str: ...

HISTORY_WORDS = re.compile(
    r"\b(previous|previously|earlier|old|older|former|formerly|history|historical|original|"
    r"superseded|outdated|used to|before|changed|change|v1|version 1|last year)\b",
    re.IGNORECASE)
SUPERSEDED_WARNING = ("Some sources are superseded (outdated) document versions, shown because "
                      "you asked about earlier rules. The current version is marked 'current'.")
TOKEN = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset("a an and are as at be by can do does for from how i in is it my of on or our the "
                      "to we what when where which who with you your".split())


def asks_for_history(question: str) -> bool:
    """True if the user seems to ask about earlier (superseded) rules."""
    return bool(HISTORY_WORDS.search(question))


def tokenize(text: str) -> list[str]:
    """Lower-case word tokens without stop words (for BM25)."""
    return [t for t in TOKEN.findall(text.lower()) if t not in STOPWORDS]


def reciprocal_rank_fusion(rankings: list[list[str]], k: int) -> list[str]:
    """Merge ranked id lists: score = sum of 1 / (k + rank); best first."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores, key=lambda i: (-scores[i], i))


@dataclass(frozen=True)
class Retrieval:
    """Search result: hits, whether superseded documents were included, and a warning."""

    hits: list[Hit]
    include_superseded: bool
    warning: str | None

    @property
    def top_score(self) -> float:
        """Best embedding similarity among the hits (0.0 when nothing was found)."""
        return max((h.score for h in self.hits), default=0.0)


class Retriever:
    """Top-k search over the indexed company documents."""

    def __init__(self, cfg: Config | None = None, store: VectorStore | None = None,
                 reranker: Reranker | None = None, llm: SupportsChat | None = None) -> None:
        self.cfg = cfg or load_config()
        self.store = store or VectorStore(self.cfg.path("chroma"), collection_name(self.cfg))
        self.reranker = reranker or (Reranker(self.cfg.reranker.model) if self.cfg.reranker.enabled else None)
        self.llm = llm  # only used for query expansion (retrieval.query_expansion > 0)
        self._bm25: tuple[int, list[Chunk], BM25Okapi] | None = None
        self._expansions: dict[str, list[str]] = {}

    # ------------------------------------------------------------------ query expansion
    def _expand(self, question: str) -> list[str]:
        """Alternative phrasings of ``question`` from the local model (synonyms, formal policy wording).

        Employees ask "time off" where the policy says "vacation"; searching with a few rewordings as well finds
        the passage either way. Returns ``[]`` when expansion is off, no model is available or the model fails.
        """
        wanted = int(self.cfg.retrieval.get("query_expansion", 0) or 0)
        if wanted <= 0 or (self.llm is None and question not in self._expansions):
            return []
        if question not in self._expansions:
            try:
                data = json.loads(self.llm.chat(EXPANSION_PROMPT.format(n=wanted), question, json_mode=True))
                queries = data.get("queries", []) if isinstance(data, dict) else []
            except Exception as exc:  # noqa: BLE001 - model missing, timeout, invalid JSON: search without expansion
                log.info("Query expansion skipped (%s)", type(exc).__name__)
                queries = []
            clean = [" ".join(q.split()) for q in queries if isinstance(q, str) and 3 <= len(q.strip()) <= MAX_EXPANSION_CHARS]
            self._expansions[question] = [q for q in clean if q.lower() != question.lower()][:wanted]
        return self._expansions[question]

    # ------------------------------------------------------------------ keyword index
    def _keyword_index(self) -> tuple[list[Chunk], BM25Okapi]:
        """BM25 over all chunks; rebuilt when the number of stored chunks changes."""
        count = self.store.count()
        if self._bm25 is None or self._bm25[0] != count:
            chunks = self.store.all_chunks()
            corpus = [tokenize(f"{c.doc_title} {c.section} {c.text}") for c in chunks]
            self._bm25 = (count, chunks, BM25Okapi(corpus))
        return self._bm25[1], self._bm25[2]

    def _keyword_ranking(self, question: str, include_superseded: bool, limit: int) -> list[Chunk]:
        chunks, bm25 = self._keyword_index()
        scores = bm25.get_scores(tokenize(question))
        order = sorted(range(len(chunks)), key=lambda i: -scores[i])
        return [chunks[i] for i in order
                if scores[i] > 0 and (include_superseded or chunks[i].status == "current")][:limit]

    # ------------------------------------------------------------------ retrieval
    def retrieve(self, question: str, include_superseded: bool | None = None) -> Retrieval:
        """Search; superseded documents are excluded unless the question asks about history."""
        if include_superseded is None:
            include_superseded = asks_for_history(question)
        r = self.cfg.retrieval
        model = self.cfg.embeddings.model
        vector = embed([question], model, "query")[0]
        pooled = r.candidates if (r.hybrid or self.reranker or r.get("query_expansion")) else r.top_k
        semantic = self.store.search(vector, pooled, include_superseded)
        by_id = {h.chunk.chunk_id: h for h in semantic}
        rankings = [[h.chunk.chunk_id for h in semantic]]
        extra: dict[str, Chunk] = {}  # candidates found only by the other searches
        alternatives = self._expand(question)
        for alt in alternatives:  # the same question in other words, searched by meaning
            found = self.store.search(embed([alt], model, "query")[0], pooled, include_superseded)
            rankings.append([h.chunk.chunk_id for h in found])
            extra.update({h.chunk.chunk_id: h.chunk for h in found})
        if r.hybrid:  # keywords of the question and its rewordings
            keyword = self._keyword_ranking(" ".join([question, *alternatives]), include_superseded, r.candidates)
            rankings.append([c.chunk_id for c in keyword])
            extra.update({c.chunk_id: c for c in keyword})
        missing = [c for cid, c in extra.items() if cid not in by_id]
        vectors = self.store.embeddings_for([c.chunk_id for c in missing])
        for chunk in missing:  # Hit.score is always the cosine to the ORIGINAL question
            cosine = float(np.dot(vector, vectors[chunk.chunk_id]))
            by_id[chunk.chunk_id] = Hit(chunk, round(cosine, 4))
        order = reciprocal_rank_fusion(rankings, r.rrf_k) if len(rankings) > 1 else rankings[0]
        order = order[: r.candidates if self.reranker else r.top_k]
        hits = [by_id[i] for i in order]
        if self.reranker and hits:
            scores = self.reranker.score(question, [h.chunk for h in hits])
            ranked = sorted(zip(scores, hits, strict=True), key=lambda p: -p[0])
            hits = [replace(h, rerank_score=round(s, 4)) for s, h in ranked]
        hits = hits[: r.top_k]
        has_old = any(h.chunk.status == "superseded" for h in hits)
        return Retrieval(hits, include_superseded, SUPERSEDED_WARNING if has_old else None)
