"""ChromaDB persistent vector store for document chunks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import chromadb

from nexora.config import Config
from nexora.rag.chunking import Chunk

DEFAULT_EMBEDDING = "sentence-transformers/all-MiniLM-L6-v2"


def collection_name(cfg: Config) -> str:
    """Chroma collection of the configured embedding model.

    Vectors of different models cannot be mixed, so each model gets its own collection; the default model keeps
    the plain ``retrieval.collection`` name (existing indexes stay valid).
    """
    base, model = cfg.retrieval.collection, cfg.embeddings.model
    if model == DEFAULT_EMBEDDING:
        return base
    return f"{base}__{re.sub(r'[^a-z0-9]+', '-', model.lower()).strip('-')}"


class EmptyIndexError(RuntimeError):
    """Raised when a search is attempted before any document was indexed."""

    def __init__(self) -> None:
        super().__init__("The document index is empty. Run `make index` "
                         "(python -m scripts.build_index) first.")


@dataclass(frozen=True)
class Hit:
    """A retrieved chunk with its similarity score (cosine, 0 to 1 for related text)."""

    chunk: Chunk
    score: float
    rerank_score: float | None = None  # set when the cross-encoder reranker ordered the results


def _to_metadata(c: Chunk) -> dict[str, str | int]:
    return {"source": c.source, "doc_title": c.doc_title, "version": c.version,
            "effective_date": c.effective_date, "status": c.status, "owner": c.owner,
            "section": c.section, "page": c.page if c.page is not None else -1}


def _from_row(chunk_id: str, text: str, meta: dict) -> Chunk:
    page = meta["page"]
    return Chunk(chunk_id=chunk_id, source=meta["source"], doc_title=meta["doc_title"],
                 version=meta["version"], effective_date=meta["effective_date"],
                 status=meta["status"], owner=meta["owner"], section=meta["section"],
                 page=None if page < 0 else page, text=text)


class VectorStore:
    """Thin wrapper around one Chroma collection using cosine distance."""

    def __init__(self, path: Path, collection: str) -> None:
        path.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(path))
        self._col = client.get_or_create_collection(collection, metadata={"hnsw:space": "cosine"})

    def count(self) -> int:
        """Number of stored chunks."""
        return self._col.count()

    def add(self, chunks: list[Chunk], embeddings: list[list[float]]) -> None:
        """Store chunks (the stored text is the passage, not the embedding header)."""
        if chunks:
            self._col.add(ids=[c.chunk_id for c in chunks], embeddings=embeddings,
                          documents=[c.text for c in chunks],
                          metadatas=[_to_metadata(c) for c in chunks])

    def delete_source(self, source: str) -> None:
        """Remove every chunk that came from the file ``source``."""
        self._col.delete(where={"source": source})

    def all_chunks(self) -> list[Chunk]:
        """Every stored chunk (used to build the keyword index)."""
        res = self._col.get(include=["documents", "metadatas"])
        return [_from_row(i, doc, meta)
                for i, doc, meta in zip(res["ids"], res["documents"], res["metadatas"], strict=True)]

    def embeddings_for(self, chunk_ids: list[str]) -> dict[str, list[float]]:
        """Stored embeddings of the given chunks."""
        if not chunk_ids:
            return {}
        res = self._col.get(ids=chunk_ids, include=["embeddings"])
        return {i: list(map(float, e)) for i, e in zip(res["ids"], res["embeddings"], strict=True)}

    def search(self, embedding: list[float], k: int, include_superseded: bool) -> list[Hit]:
        """Top-``k`` chunks by cosine similarity; superseded documents only if asked."""
        if self.count() == 0:
            raise EmptyIndexError
        where = None if include_superseded else {"status": "current"}
        res = self._col.query(query_embeddings=[embedding], n_results=k, where=where)
        return [Hit(_from_row(i, doc, meta), round(1.0 - dist, 4))
                for i, doc, meta, dist in zip(res["ids"][0], res["documents"][0],
                                              res["metadatas"][0], res["distances"][0],
                                              strict=True)]
