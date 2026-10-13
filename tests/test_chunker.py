"""Tests for the character chunker and the end-to-end pipeline."""

from datetime import datetime, timezone
from pathlib import Path

import pydantic
import pytest

from app.ingestion.chunker import CharacterChunker, make_chunk_id
from app.ingestion.models import Document, SkipReason
from app.ingestion.pipeline import run_pipeline
from tests.helpers import FIXTURE_FILES, make_pdf, make_settings


def make_doc(content: str, file_path: str = "notes.txt", **metadata: object) -> Document:
    return Document(
        id="doc-1",
        file_path=file_path,
        file_type=file_path.rsplit(".", 1)[-1],
        project_name="proj",
        title=file_path,
        content=content,
        modified_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        size_bytes=len(content),
        metadata=dict(metadata),
    )


WORDS = " ".join(f"word{i}" for i in range(400))  # ~3 KB, single line


def test_chunks_respect_size_and_overlap() -> None:
    chunker = CharacterChunker(chunk_size=200, overlap=40)
    doc = make_doc(WORDS)
    chunks = chunker.chunk(doc)

    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk.content) <= 200
        assert chunk.content == doc.content[chunk.start_char:chunk.end_char]
    for prev, nxt in zip(chunks, chunks[1:]):
        assert nxt.start_char == prev.end_char - 40
    assert chunks[0].start_char == 0
    assert chunks[-1].end_char == len(doc.content)


def test_breaks_at_word_boundaries() -> None:
    chunks = CharacterChunker(chunk_size=200, overlap=0).chunk(make_doc(WORDS))
    for chunk in chunks[:-1]:
        assert chunk.content.endswith(" ")  # never cut mid-word


def test_prefers_paragraph_breaks() -> None:
    text = ("a" * 60 + "\n\n") + ("b" * 60 + "\n") + "c" * 100
    chunks = CharacterChunker(chunk_size=100, overlap=0).chunk(make_doc(text))
    assert chunks[0].content == "a" * 60 + "\n\n"


def test_hard_cut_when_no_boundary() -> None:
    chunks = CharacterChunker(chunk_size=100, overlap=10).chunk(make_doc("x" * 250))
    assert [(c.start_char, c.end_char) for c in chunks] == [(0, 100), (90, 190), (180, 250)]


def test_chunk_ids_are_stable_and_unique() -> None:
    chunker = CharacterChunker(chunk_size=200, overlap=40)
    first = chunker.chunk(make_doc(WORDS))
    second = chunker.chunk(make_doc(WORDS))
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    assert len({c.chunk_id for c in first}) == len(first)
    assert first[0].chunk_id == make_chunk_id("notes.txt", 0, first[0].content)


def test_chunk_metadata_is_copied_from_document() -> None:
    chunk = CharacterChunker().chunk(make_doc("hello world", file_path="src/app.py"))[0]
    assert chunk.document_id == "doc-1"
    assert chunk.file_path == "src/app.py"
    assert chunk.file_type == "py"
    assert chunk.project_name == "proj"
    assert chunk.chunk_index == 0
    assert chunk.metadata["title"] == "src/app.py"


def test_pdf_chunks_record_page_numbers() -> None:
    content = "page one text\n\npage two text"
    pages = [{"page": 1, "start_char": 0, "end_char": 13}, {"page": 2, "start_char": 15, "end_char": 28}]
    chunks = CharacterChunker(chunk_size=15, overlap=0).chunk(make_doc(content, "a.pdf", pages=pages))
    assert chunks[0].metadata["pages"] == [1]
    assert chunks[-1].metadata["pages"] == [2]


def test_empty_and_whitespace_documents_produce_no_chunks() -> None:
    chunker = CharacterChunker(chunk_size=10, overlap=2)
    assert chunker.chunk(make_doc("")) == []
    assert chunker.chunk(make_doc("   \n\n  ")) == []


@pytest.mark.parametrize(("size", "overlap"), [(0, 0), (10, 10), (10, 20), (10, -1)])
def test_invalid_chunker_config_is_rejected(size: int, overlap: int) -> None:
    with pytest.raises(ValueError):
        CharacterChunker(chunk_size=size, overlap=overlap)


def test_invalid_settings_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(pydantic.ValidationError):
        make_settings(tmp_path, chunk_size=100, chunk_overlap=100)


@pytest.mark.parametrize(("size", "overlap"), [(1, 0), (2, 1), (5, 4), (10, 9), (7, 6)])
def test_tiny_chunks_with_large_overlap_always_progress(size: int, overlap: int) -> None:
    # Many short breaks: a naive "end - overlap" would step backwards and loop forever.
    text = "a b\nc d\n\ne f " * 50
    spans = CharacterChunker(chunk_size=size, overlap=overlap).split(text)
    starts = [s for s, _ in spans]
    assert all(b > a for a, b in zip(starts, starts[1:]))
    assert spans[-1][1] == len(text)
    assert len(spans) <= len(text)


def test_loop_guard_raises_instead_of_hanging(monkeypatch: pytest.MonkeyPatch) -> None:
    chunker = CharacterChunker(chunk_size=10, overlap=5)
    monkeypatch.setattr(chunker, "_next_start", lambda start, end: start)  # never advances
    with pytest.raises(RuntimeError, match="no progress"):
        chunker.split("x" * 100)


# ---- end-to-end -----------------------------------------------------------


def test_pipeline_survives_corrupted_pdf(project: Path) -> None:
    (project / "broken.pdf").write_bytes(b"%PDF-1.4\x00\x00\xff garbage, not a real pdf")
    make_pdf(project / "good.pdf", ["A valid PDF page"])

    result = run_pipeline(make_settings(project, chunk_size=200, chunk_overlap=20))

    skipped = {s.path: s.reason for s in result.skipped}
    assert skipped["broken.pdf"] == SkipReason.PARSE_ERROR  # not BINARY
    parsed = {d.file_path for d in result.documents}
    assert parsed == FIXTURE_FILES | {"good.pdf"}
    assert {c.file_path for c in result.chunks} == parsed
    assert result.files_found == len(FIXTURE_FILES) + 2  # broken.pdf was found, then failed to parse
