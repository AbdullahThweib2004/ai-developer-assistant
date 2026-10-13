"""Split documents into overlapping chunks.

``Chunker`` is the interface; ``CharacterChunker`` is the baseline
implementation. A code-aware chunker (e.g. splitting Python by functions and
classes via ``ast``) can be added later by implementing the same protocol.
"""

import hashlib
from typing import Any, Protocol

from app.ingestion.models import Chunk, Document

# Preferred break points, best first.
_SEPARATORS = ("\n\n", "\n", " ")


class Chunker(Protocol):
    """Anything that can turn a Document into Chunks."""

    def chunk(self, document: Document) -> list[Chunk]: ...


class CharacterChunker:
    """Fixed-size character chunks with overlap, breaking at natural boundaries.

    Each chunk is at most ``chunk_size`` characters. If possible the chunk ends
    just after a paragraph break, else a line break, else a space, as long as
    that boundary is in the second half of the chunk. The next chunk starts
    ``overlap`` characters before the previous one ended.
    """

    def __init__(self, chunk_size: int = 1000, overlap: int = 150) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be > 0")
        if not 0 <= overlap < chunk_size:
            raise ValueError("overlap must be >= 0 and < chunk_size")
        self.chunk_size = chunk_size
        self.overlap = overlap

    def split(self, text: str) -> list[tuple[int, int]]:
        """Return ``(start, end)`` character spans covering ``text``."""
        spans: list[tuple[int, int]] = []
        start, length = 0, len(text)
        # Safety net: each step advances by at least one character, so more
        # iterations than this means the progress rule is broken.
        max_iterations = length + 1
        while start < length:
            if len(spans) >= max_iterations:
                raise RuntimeError("Chunker made no progress; aborting to avoid an infinite loop")
            end = min(start + self.chunk_size, length)
            if end < length:
                end = self._find_break(text, start, end)
            spans.append((start, end))
            if end >= length:
                break
            start = self._next_start(start, end)
        return spans

    def chunk(self, document: Document) -> list[Chunk]:
        chunks: list[Chunk] = []
        for start, end in self.split(document.content):
            content = document.content[start:end]
            if not content.strip():
                continue
            index = len(chunks)
            chunks.append(
                Chunk(
                    chunk_id=make_chunk_id(document.file_path, index, content),
                    document_id=document.id,
                    file_path=document.file_path,
                    file_type=document.file_type,
                    project_name=document.project_name,
                    chunk_index=index,
                    content=content,
                    start_char=start,
                    end_char=end,
                    metadata=_chunk_metadata(document, start, end),
                )
            )
        return chunks

    def _next_start(self, start: int, end: int) -> int:
        """Start of the next chunk: ``overlap`` before ``end``, but always forward."""
        return max(end - self.overlap, start + 1)

    def _find_break(self, text: str, start: int, end: int) -> int:
        """Best boundary in the second half of ``text[start:end]``, or ``end``."""
        earliest = start + self.chunk_size // 2
        for sep in _SEPARATORS:
            idx = text.rfind(sep, earliest, end)
            if idx != -1:
                return idx + len(sep)
        return end


def make_chunk_id(file_path: str, chunk_index: int, content: str) -> str:
    """Deterministic ID: same file, position and content -> same ID."""
    key = f"{file_path}\x00{chunk_index}\x00{content}"
    return hashlib.sha256(key.encode()).hexdigest()


def _chunk_metadata(document: Document, start: int, end: int) -> dict[str, Any]:
    metadata: dict[str, Any] = {"title": document.title}
    pages = document.metadata.get("pages")
    if pages:
        metadata["pages"] = [
            p["page"] for p in pages if p["start_char"] < end and p["end_char"] > start
        ]
    return metadata
