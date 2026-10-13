"""Turn retrieved chunks into a numbered, delimited context for the LLM.

Steps: merge overlapping chunks from the same file -> order by score ->
keep the best sources that fit the character budget (dropping the lowest
scored first, never truncating a chunk) -> number them S1, S2, ... and wrap
each one in ``<source>`` tags.
"""

import html
import re
from dataclasses import dataclass, field

from pydantic import BaseModel

from app.retrieval.retriever import RetrievedChunk

# Neutralize tags inside source text so it can't close or open a <source> block.
_TAG_PATTERN = re.compile(r"<(/?)(sources?)\b", re.IGNORECASE)


class SourceRef(BaseModel):
    """One numbered source shown to the LLM."""

    source_id: str  # "S1", "S2", ...
    file_path: str
    file_type: str
    start_char: int
    end_char: int
    pages: str | None = None  # PDFs only, e.g. "2-3"
    score: float
    chunk_ids: list[str]
    content: str

    @property
    def location(self) -> str:
        loc = f"chars {self.start_char}-{self.end_char}"
        return f"{loc}, pages {self.pages}" if self.pages else loc


class DroppedChunk(BaseModel):
    """A retrieved chunk that didn't make it into the context."""

    chunk_id: str
    file_path: str
    score: float
    reason: str


@dataclass
class BuiltContext:
    """The context string plus everything needed to interpret citations."""

    text: str
    sources: dict[str, SourceRef] = field(default_factory=dict)
    dropped: list[DroppedChunk] = field(default_factory=list)
    merged_chunks: int = 0  # chunks absorbed into an overlapping neighbor


@dataclass
class _Span:
    file_path: str
    file_type: str
    start: int
    end: int
    content: str
    score: float
    chunk_ids: list[str]
    pages: set[int]


def build_context(chunks: list[RetrievedChunk], max_chars: int) -> BuiltContext:
    """Build the LLM context from ``chunks`` within ``max_chars`` characters."""
    spans, merged = _merge_overlaps(chunks)
    spans.sort(key=lambda s: s.score, reverse=True)

    blocks: list[str] = []
    sources: dict[str, SourceRef] = {}
    dropped: list[DroppedChunk] = []
    used = 0
    budget_exhausted = False
    for span in spans:
        source_id = f"S{len(sources) + 1}"
        ref = _to_ref(source_id, span)
        block = format_source(ref)
        cost = len(block) + (2 if blocks else 0)  # blank line between blocks
        if budget_exhausted or used + cost > max_chars:
            # Drop this and every lower-scored span, never cutting one in half.
            budget_exhausted = True
            dropped.extend(
                DroppedChunk(chunk_id=cid, file_path=span.file_path, score=span.score, reason="context_budget")
                for cid in span.chunk_ids
            )
            continue
        blocks.append(block)
        sources[source_id] = ref
        used += cost

    return BuiltContext(text="\n\n".join(blocks), sources=sources, dropped=dropped, merged_chunks=merged)


def format_source(ref: SourceRef) -> str:
    """``<source id=... file=... chars=...>`` wrapper with escaped content."""
    attrs = f'id="{ref.source_id}" file="{html.escape(ref.file_path, quote=True)}" chars="{ref.start_char}-{ref.end_char}"'
    if ref.pages:
        attrs += f' pages="{ref.pages}"'
    return f"<source {attrs}>\n{escape_source_text(ref.content)}\n</source>"


def escape_source_text(text: str) -> str:
    """Make ``<source``/``</source`` inside file text harmless (``&lt;source``)."""
    return _TAG_PATTERN.sub(r"&lt;\1\2", text)


def _merge_overlaps(chunks: list[RetrievedChunk]) -> tuple[list[_Span], int]:
    """Combine chunks of the same file whose character ranges overlap."""
    by_file: dict[str, list[RetrievedChunk]] = {}
    for chunk in chunks:
        by_file.setdefault(chunk.file_path, []).append(chunk)

    spans: list[_Span] = []
    merged = 0
    for file_chunks in by_file.values():
        file_chunks.sort(key=lambda c: c.start_char)
        current: _Span | None = None
        for chunk in file_chunks:
            if current is not None and chunk.start_char < current.end:
                # Overlap: append only the part of this chunk not already covered.
                if chunk.end_char > current.end:
                    current.content += chunk.content[current.end - chunk.start_char:]
                    current.end = chunk.end_char
                current.score = max(current.score, chunk.score)
                current.chunk_ids.append(chunk.chunk_id)
                current.pages |= _pages(chunk)
                merged += 1
                continue
            current = _Span(
                file_path=chunk.file_path, file_type=chunk.file_type, start=chunk.start_char,
                end=chunk.end_char, content=chunk.content, score=chunk.score,
                chunk_ids=[chunk.chunk_id], pages=_pages(chunk),
            )
            spans.append(current)
    return spans, merged


def _pages(chunk: RetrievedChunk) -> set[int]:
    raw = chunk.metadata.get("pages")
    if not raw:
        return set()
    return {int(p) for p in str(raw).split(",") if p.strip().isdigit()}


def _to_ref(source_id: str, span: _Span) -> SourceRef:
    pages = None
    if span.pages:
        low, high = min(span.pages), max(span.pages)
        pages = str(low) if low == high else f"{low}-{high}"
    return SourceRef(
        source_id=source_id, file_path=span.file_path, file_type=span.file_type,
        start_char=span.start, end_char=span.end, pages=pages, score=span.score,
        chunk_ids=span.chunk_ids, content=span.content,
    )
