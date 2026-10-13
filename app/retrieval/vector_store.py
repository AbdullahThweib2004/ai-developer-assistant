"""A thin wrapper around a persistent Chroma collection (one per project)."""

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import chromadb
from chromadb.api.models.Collection import Collection
from chromadb.config import Settings as ChromaSettings
from chromadb.errors import NotFoundError

from app.ingestion.models import Chunk

logger = logging.getLogger(__name__)

MetadataValue = str | int | float | bool
MODEL_KEY = "embedding_model"


class VectorStoreError(Exception):
    """Base class for vector store errors."""


class ProjectNotIndexedError(VectorStoreError):
    """The project's collection does not exist yet."""


class EmbeddingModelMismatchError(VectorStoreError):
    """The collection was built with a different embedding model."""


@dataclass
class QueryResult:
    """One raw hit from Chroma."""

    chunk_id: str
    content: str
    metadata: dict[str, MetadataValue]
    distance: float

    @property
    def similarity(self) -> float:
        """Cosine similarity: 1 - cosine distance (1.0 = same direction)."""
        return 1.0 - self.distance


MAX_COLLECTION_NAME = 63  # Chroma allows more, but short names are easier to read


def collection_name(prefix: str, project_path: Path) -> str:
    """``<prefix>_<safe_folder_name>_<hash8>`` for a project directory.

    The 8-character hash of the resolved absolute path keeps two different
    folders with the same name apart. The result only uses ``[a-z0-9_-]``,
    starts with the prefix and ends with the hash, as Chroma requires.
    """
    resolved = project_path.resolve()
    digest = hashlib.sha256(str(resolved).encode()).hexdigest()[:8]
    safe = re.sub(r"[^a-z0-9_-]+", "_", resolved.name.lower()).strip("_-") or "default"
    room = MAX_COLLECTION_NAME - len(prefix) - len(digest) - 2
    safe = safe[:room].strip("_-") or "default"
    return f"{prefix}_{safe}_{digest}"


def to_chroma_metadata(metadata: dict[str, Any]) -> dict[str, MetadataValue]:
    """Keep only values Chroma accepts (str/int/float/bool).

    ``None`` is dropped, datetimes become ISO strings, lists of scalars become
    comma-separated strings, and anything else becomes JSON text.
    """
    flat: dict[str, MetadataValue] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, (str, int, float, bool)):
            flat[key] = value
        elif isinstance(value, datetime):
            flat[key] = value.isoformat()
        elif isinstance(value, (list, tuple)) and all(isinstance(v, (str, int, float)) for v in value):
            if value:
                flat[key] = ",".join(str(v) for v in value)
        else:
            flat[key] = json.dumps(value, default=str)
    return flat


def chunk_metadata(chunk: Chunk, extra: dict[str, Any] | None = None) -> dict[str, MetadataValue]:
    """The metadata stored with each chunk in Chroma."""
    metadata: dict[str, Any] = {
        "document_id": chunk.document_id,
        "file_path": chunk.file_path,
        "file_type": chunk.file_type,
        "project_name": chunk.project_name,
        "chunk_index": chunk.chunk_index,
        "start_char": chunk.start_char,
        "end_char": chunk.end_char,
        "title": chunk.metadata.get("title"),
    }
    pages = chunk.metadata.get("pages")
    if pages:
        metadata["pages"] = pages
        metadata["page_start"] = min(pages)
        metadata["page_end"] = max(pages)
    metadata.update(extra or {})
    return to_chroma_metadata(metadata)


class ChromaVectorStore:
    """One project's chunks in a persistent Chroma collection (cosine space).

    ``embedding_model`` must match the model the collection was built with.
    Pass ``None`` to open an existing collection for inspection only
    (count, list files, read the stored model) without that check.
    """

    def __init__(self, persist_dir: Path, name: str, embedding_model: str | None) -> None:
        self.persist_dir = persist_dir
        self.name = name
        self.embedding_model = embedding_model
        self._client = chromadb.PersistentClient(
            path=str(persist_dir), settings=ChromaSettings(anonymized_telemetry=False)
        )

    # ---- lifecycle ------------------------------------------------------

    def exists(self) -> bool:
        try:
            self._client.get_collection(self.name)
        except NotFoundError:
            return False
        return True

    def delete_collection(self) -> None:
        """Delete the collection; does nothing if it doesn't exist."""
        try:
            self._client.delete_collection(self.name)
        except NotFoundError:
            pass

    def stored_model(self) -> str | None:
        """Embedding model recorded in the collection metadata."""
        value = (self._collection().metadata or {}).get(MODEL_KEY)
        return str(value) if value is not None else None

    # ---- data -----------------------------------------------------------

    def upsert_chunks(
        self,
        chunks: list[Chunk],
        embeddings: list[list[float]],
        extra_metadata: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Insert or replace chunks. ``extra_metadata`` maps document_id -> fields."""
        if len(chunks) != len(embeddings):
            raise ValueError("chunks and embeddings must have the same length")
        self._require_model()
        if not chunks:
            return
        collection = self._collection(create=True)
        extra_metadata = extra_metadata or {}
        batch = self._client.get_max_batch_size()
        for i in range(0, len(chunks), batch):
            part = chunks[i:i + batch]
            collection.upsert(
                ids=[c.chunk_id for c in part],
                embeddings=embeddings[i:i + batch],  # type: ignore[arg-type]
                documents=[c.content for c in part],
                metadatas=[chunk_metadata(c, extra_metadata.get(c.document_id)) for c in part],
            )

    def query(
        self, embedding: list[float], top_k: int, where: dict[str, Any] | None = None
    ) -> list[QueryResult]:
        """Nearest chunks to ``embedding``, closest first."""
        self._require_model()
        collection = self._collection()
        if top_k <= 0 or collection.count() == 0:
            return []
        raw = collection.query(
            query_embeddings=[embedding],  # type: ignore[arg-type]
            n_results=top_k,
            where=where,
            include=["documents", "metadatas", "distances"],
        )
        ids = raw["ids"][0]
        documents = (raw["documents"] or [[]])[0]
        metadatas = (raw["metadatas"] or [[]])[0]
        distances = (raw["distances"] or [[]])[0]
        return [
            QueryResult(chunk_id=i, content=d or "", metadata=dict(m or {}), distance=float(dist))
            for i, d, m, dist in zip(ids, documents, metadatas, distances)
        ]

    def count(self) -> int:
        return self._collection().count()

    def list_indexed_files(self) -> list[str]:
        """Sorted, unique file paths present in the collection."""
        result = self._collection().get(include=["metadatas"])
        return sorted({str(m["file_path"]) for m in result["metadatas"] or [] if m and "file_path" in m})

    # ---- helpers --------------------------------------------------------

    def _require_model(self) -> None:
        if self.embedding_model is None:
            raise VectorStoreError("This store was opened for inspection only (no embedding model)")

    def _collection(self, create: bool = False) -> Collection:
        """Get (or create) the collection and check its embedding model."""
        try:
            collection = self._client.get_collection(self.name, embedding_function=None)
        except NotFoundError:
            if not create or self.embedding_model is None:
                raise ProjectNotIndexedError(
                    f"Project is not indexed (no collection '{self.name}' in {self.persist_dir}). "
                    "Run: python -m app.cli index --path <repo>"
                ) from None
            logger.info("Creating collection %s", self.name)
            return self._client.create_collection(
                self.name,
                metadata={"hnsw:space": "cosine", MODEL_KEY: self.embedding_model},
                embedding_function=None,
            )

        stored = (collection.metadata or {}).get(MODEL_KEY)
        if self.embedding_model is not None and stored != self.embedding_model:
            raise EmbeddingModelMismatchError(
                f"Collection '{self.name}' was built with embedding model '{stored}', "
                f"but '{self.embedding_model}' is configured. Rebuild the index with "
                "`python -m app.cli index --path <repo> --rebuild` or switch back to the original model."
            )
        return collection
