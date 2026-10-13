"""Semantic search over an indexed project: query -> ranked chunks.

``search`` is a small sequence of steps (validate -> filter -> vector search ->
score threshold -> rank) so later steps such as hybrid keyword search or a
reranker can be inserted without changing callers.
"""

from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.retrieval.embeddings import EmbeddingProvider, get_embedding_provider
from app.retrieval.indexer import open_store
from app.retrieval.vector_store import ChromaVectorStore, QueryResult

# Metadata keys promoted to top-level fields of RetrievedChunk.
_PROMOTED = {"file_path", "file_type", "chunk_index", "start_char", "end_char"}


class InvalidQueryError(ValueError):
    """The search request is invalid (e.g. an empty query)."""


class RetrievedChunk(BaseModel):
    """A chunk returned by a search, with its similarity score."""

    chunk_id: str
    content: str
    file_path: str
    file_type: str
    score: float  # 1 - cosine distance; higher is more similar
    chunk_index: int
    start_char: int
    end_char: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class Retriever:
    """Embeds a query and returns the most similar chunks of one project."""

    def __init__(
        self,
        store: ChromaVectorStore,
        provider: EmbeddingProvider,
        default_top_k: int = 5,
        default_min_score: float = 0.0,
    ) -> None:
        self.store = store
        self.provider = provider
        self.default_top_k = default_top_k
        self.default_min_score = default_min_score

    @classmethod
    def for_project(
        cls,
        project_path: Path,
        settings: Settings | None = None,
        provider: EmbeddingProvider | None = None,
    ) -> "Retriever":
        settings = settings or get_settings()
        provider = provider or get_embedding_provider(settings)
        store = open_store(settings, project_path, provider.name)
        return cls(store, provider, settings.retrieval_top_k, settings.retrieval_min_score)

    def search(
        self,
        query: str,
        top_k: int | None = None,
        min_score: float | None = None,
        file_types: list[str] | None = None,
    ) -> list[RetrievedChunk]:
        """Return up to ``top_k`` chunks with score >= ``min_score``, best first."""
        query = self._validate(query, top_k)
        top_k = top_k or self.default_top_k
        min_score = self.default_min_score if min_score is None else min_score

        self.store.count()  # raises ProjectNotIndexedError before loading a model
        hits = self._vector_search(query, top_k, self._build_filter(file_types))
        hits = [h for h in hits if h.similarity >= min_score]
        return self._rank(hits)

    # ---- steps ------------------------------------------------------------

    @staticmethod
    def _validate(query: str, top_k: int | None) -> str:
        if not query or not query.strip():
            raise InvalidQueryError("Query must not be empty")
        if top_k is not None and top_k <= 0:
            raise InvalidQueryError("top_k must be > 0")
        return query.strip()

    @staticmethod
    def _build_filter(file_types: list[str] | None) -> dict[str, Any] | None:
        if not file_types:
            return None
        types = [t.lower().lstrip(".") for t in file_types]
        return {"file_type": {"$in": types}}

    def _vector_search(self, query: str, top_k: int, where: dict[str, Any] | None) -> list[QueryResult]:
        return self.store.query(self.provider.embed_query(query), top_k, where)

    @staticmethod
    def _rank(hits: list[QueryResult]) -> list[RetrievedChunk]:
        hits = sorted(hits, key=lambda h: h.similarity, reverse=True)
        return [
            RetrievedChunk(
                chunk_id=h.chunk_id,
                content=h.content,
                file_path=str(h.metadata["file_path"]),
                file_type=str(h.metadata["file_type"]),
                score=round(h.similarity, 6),
                chunk_index=int(h.metadata["chunk_index"]),
                start_char=int(h.metadata["start_char"]),
                end_char=int(h.metadata["end_char"]),
                metadata={k: v for k, v in h.metadata.items() if k not in _PROMOTED},
            )
            for h in hits
        ]
