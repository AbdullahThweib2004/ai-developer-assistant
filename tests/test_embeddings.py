"""Tests for embedding providers."""

import math
from pathlib import Path

import pydantic
import pytest

from app.retrieval.embeddings import (
    FakeEmbeddingProvider,
    SentenceTransformerProvider,
    get_embedding_provider,
)
from tests.helpers import make_settings


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=False))  # vectors are normalized


def test_fake_provider_is_deterministic_and_normalized() -> None:
    provider = FakeEmbeddingProvider(dimension=64)
    first = provider.embed_query("verify user credentials")
    assert first == provider.embed_query("verify user credentials")
    assert len(first) == provider.dimension == 64
    assert math.isclose(math.sqrt(sum(v * v for v in first)), 1.0)


def test_fake_provider_similar_texts_are_closer() -> None:
    provider = FakeEmbeddingProvider()
    query = provider.embed_query("database connection retry")
    related = provider.embed_query("retry the database connection with backoff")
    unrelated = provider.embed_query("render the login page template")
    assert cosine(query, related) > cosine(query, unrelated)


def test_fake_provider_batch_matches_single_and_handles_empty() -> None:
    provider = FakeEmbeddingProvider()
    texts = ["alpha beta", "gamma", ""]
    assert provider.embed_documents(texts) == [provider.embed_query(t) for t in texts]
    assert any(provider.embed_query(""))  # never a zero vector
    assert provider.embed_documents([]) == []


def test_factory_selects_provider(tmp_path: Path) -> None:
    fake = get_embedding_provider(make_settings(tmp_path, embedding_provider="fake"))
    assert isinstance(fake, FakeEmbeddingProvider)

    real = get_embedding_provider(make_settings(tmp_path, embedding_model="some/model", embedding_batch_size=8))
    assert isinstance(real, SentenceTransformerProvider)
    assert real.name == "some/model"
    assert real.batch_size == 8


def test_sentence_transformer_provider_loads_lazily() -> None:
    provider = SentenceTransformerProvider("does-not-exist/model")
    assert provider._model is None  # nothing downloaded or loaded yet
    assert provider.embed_documents([]) == []


def test_unknown_provider_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(pydantic.ValidationError):
        make_settings(tmp_path, embedding_provider="nope")


@pytest.mark.slow
def test_real_model_embeddings() -> None:
    provider = SentenceTransformerProvider("sentence-transformers/all-MiniLM-L6-v2")
    vectors = provider.embed_documents(["How do users log in?", "User authentication and login", "Banana bread"])
    assert provider.dimension == 384
    assert all(math.isclose(math.sqrt(sum(v * v for v in vec)), 1.0, rel_tol=1e-4) for vec in vectors)
    assert cosine(vectors[0], vectors[1]) > cosine(vectors[0], vectors[2])
