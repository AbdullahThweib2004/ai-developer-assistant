"""Embedding providers: turn text into vectors.

All providers return L2-normalized vectors, so cosine similarity is a dot product.
To add a hosted embedding API later, implement :class:`EmbeddingProvider` and
register it in :func:`get_embedding_provider`.
"""

import hashlib
import math
import re
from typing import TYPE_CHECKING, Protocol

from app.config import Settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


class EmbeddingProvider(Protocol):
    """Anything that can embed documents and queries into the same vector space."""

    @property
    def name(self) -> str:
        """Identifies the model; stored with the index to detect mismatches."""
        ...

    @property
    def dimension(self) -> int: ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class SentenceTransformerProvider:
    """Local embeddings with a sentence-transformers model.

    The model is loaded on first use, so creating the provider is cheap
    (e.g. for ``stats`` commands that never embed anything).
    """

    def __init__(self, model_name: str, batch_size: int = 32, device: str | None = None) -> None:
        self.model_name = model_name
        self.batch_size = batch_size
        self.device = device
        self._model: SentenceTransformer | None = None

    @property
    def name(self) -> str:
        return self.model_name

    @property
    def dimension(self) -> int:
        dim = self._load().get_embedding_dimension()
        if dim is None:
            raise RuntimeError(f"Model {self.model_name} does not report an embedding dimension")
        return dim

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._load().encode(
            texts,
            batch_size=self.batch_size,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return vectors.tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_documents([text])[0]

    def _load(self) -> "SentenceTransformer":
        if self._model is None:
            # Imported lazily: torch is slow to import and not needed by every command.
            from sentence_transformers import SentenceTransformer
            from transformers.utils import logging as hf_logging

            hf_logging.disable_progress_bar()  # keep CLI output clean
            self._model = SentenceTransformer(self.model_name, device=self.device)
        return self._model


class FakeEmbeddingProvider:
    """Deterministic, offline embeddings for tests. Not for real use.

    A hashed bag of words: each lowercase token adds 1 to a bucket chosen by
    its hash. Texts that share words get similar vectors, which is enough to
    test ranking and filtering without downloading a model.
    """

    _TOKEN = re.compile(r"[a-z0-9]+")

    def __init__(self, dimension: int = 256) -> None:
        self._dimension = dimension

    @property
    def name(self) -> str:
        return f"fake-hashed-bow-{self._dimension}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimension
        for token in self._TOKEN.findall(text.lower()):
            bucket = int.from_bytes(hashlib.sha256(token.encode()).digest()[:4], "big")
            vector[bucket % self._dimension] += 1.0
        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            vector[0] = 1.0  # avoid a zero vector, which has no cosine direction
            return vector
        return [v / norm for v in vector]


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    """Build the provider selected by ``EMBEDDING_PROVIDER``.

    Extension point: a hosted embedding API (e.g. ``"api"``) would be added
    here as another branch returning a class that implements EmbeddingProvider.
    """
    if settings.embedding_provider == "sentence_transformers":
        return SentenceTransformerProvider(settings.embedding_model, settings.embedding_batch_size)
    if settings.embedding_provider == "fake":
        return FakeEmbeddingProvider()
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {settings.embedding_provider!r}")
