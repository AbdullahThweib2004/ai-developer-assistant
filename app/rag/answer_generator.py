"""Answer questions about a project: retrieve -> build context -> prompt -> generate -> validate.

This is the single entry point used by both the CLI and the Streamlit UI.
"""

import logging
import re
import time
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.llm.base import LLMProvider
from app.llm.factory import get_llm_provider
from app.rag.context_builder import DroppedChunk, SourceRef, build_context
from app.rag.prompts import INSUFFICIENT_MARKER, PROMPT_VERSION, SYSTEM_PROMPT, build_user_prompt
from app.retrieval.embeddings import EmbeddingProvider
from app.retrieval.retriever import InvalidQueryError, RetrievedChunk, Retriever

logger = logging.getLogger(__name__)

# [S1], [S12], and grouped forms like [S1, S3].
_CITATION_GROUP = re.compile(r"\[\s*(S\d+(?:\s*[,;]\s*S\d+)*)\s*\]")


class Latency(BaseModel):
    retrieval_ms: float = 0.0
    llm_ms: float = 0.0
    total_ms: float = 0.0


class Answer(BaseModel):
    """The result of one question, with everything needed to audit it."""

    question: str
    answer_text: str
    insufficient_evidence: bool
    cited_sources: list[SourceRef] = Field(default_factory=list)  # only sources the answer cites
    context_sources: list[SourceRef] = Field(default_factory=list)  # sources sent to the LLM
    all_retrieved_sources: list[RetrievedChunk] = Field(default_factory=list)
    dropped_sources: list[DroppedChunk] = Field(default_factory=list)
    invalid_citations: list[str] = Field(default_factory=list)  # e.g. ["S9"] if no S9 was given
    suggestions: list[str] = Field(default_factory=list)
    prompt_version: str = PROMPT_VERSION
    provider: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency: Latency = Field(default_factory=Latency)


def parse_citations(text: str) -> list[str]:
    """Source ids cited in ``text``, in order of first appearance, without duplicates."""
    ids: list[str] = []
    for group in _CITATION_GROUP.findall(text):
        for source_id in re.split(r"\s*[,;]\s*", group):
            if source_id not in ids:
                ids.append(source_id)
    return ids


def is_insufficient(text: str) -> bool:
    """True if the model flagged the sources as insufficient (marker on the first line)."""
    first_line = text.strip().splitlines()[0] if text.strip() else ""
    return INSUFFICIENT_MARKER.lower() in first_line.lower().strip("#*_ :")


class AnswerGenerator:
    """Answers questions using retrieval plus one LLM call."""

    def __init__(
        self,
        settings: Settings | None = None,
        llm: LLMProvider | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._llm = llm
        self.embedding_provider = embedding_provider

    @property
    def llm(self) -> LLMProvider:
        """Created on first use, so retrieval-only paths need no LLM configuration."""
        if self._llm is None:
            self._llm = get_llm_provider(self.settings)
        return self._llm

    def answer(
        self,
        question: str,
        project_path: Path,
        top_k: int | None = None,
        file_types: list[str] | None = None,
    ) -> Answer:
        """Answer ``question`` about the project at ``project_path``.

        Raises InvalidQueryError for an empty question, ProjectNotIndexedError if
        the project has no index, and LLMError subclasses for LLM failures.
        """
        if not question or not question.strip():
            raise InvalidQueryError("Question must not be empty")
        started = time.perf_counter()

        retriever = Retriever.for_project(project_path, self.settings, self.embedding_provider)
        retrieved = retriever.search(question, top_k=top_k, file_types=file_types)
        retrieval_ms = _ms_since(started)

        if not retrieved:
            return self._insufficient(question, [], [], retrieval_ms, started, file_types)

        context = build_context(retrieved, self.settings.rag_max_context_chars)
        if not context.sources:
            return self._insufficient(question, retrieved, context.dropped, retrieval_ms, started, file_types)

        user_prompt = build_user_prompt(question, context.text)
        llm_started = time.perf_counter()
        response = self.llm.generate(SYSTEM_PROMPT, user_prompt)
        llm_ms = _ms_since(llm_started)

        cited_ids = parse_citations(response.text)
        cited = [context.sources[i] for i in cited_ids if i in context.sources]
        invalid = [i for i in cited_ids if i not in context.sources]
        if invalid:
            logger.warning("Answer contains %d invalid citation(s)", len(invalid))

        return Answer(
            question=question,
            answer_text=response.text,
            insufficient_evidence=is_insufficient(response.text),
            cited_sources=cited,
            context_sources=list(context.sources.values()),
            all_retrieved_sources=retrieved,
            dropped_sources=context.dropped,
            invalid_citations=invalid,
            provider=response.provider,
            model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            latency=Latency(retrieval_ms=retrieval_ms, llm_ms=round(llm_ms, 1), total_ms=_ms_since(started)),
        )

    def _insufficient(
        self,
        question: str,
        retrieved: list[RetrievedChunk],
        dropped: list[DroppedChunk],
        retrieval_ms: float,
        started: float,
        file_types: list[str] | None,
    ) -> Answer:
        """Answer without calling the LLM when there is nothing to ground it on."""
        suggestions = [
            "Rephrase the question using names that appear in the code (functions, files, config keys).",
            "Re-index the project if files changed: `python -m app.cli index --path <repo> --rebuild`.",
        ]
        if file_types:
            suggestions.insert(0, f"Remove the file-type filter ({', '.join(file_types)}) to search all files.")
        if self.settings.retrieval_min_score > 0:
            suggestions.append(f"Lower RETRIEVAL_MIN_SCORE (currently {self.settings.retrieval_min_score}).")
        if dropped:
            suggestions.append("Increase RAG_MAX_CONTEXT_CHARS: the retrieved sources didn't fit the context budget.")
        text = (
            f"{INSUFFICIENT_MARKER}\n\n"
            "No relevant sources were found in the project for this question, so no answer was generated."
        )
        return Answer(
            question=question,
            answer_text=text,
            insufficient_evidence=True,
            all_retrieved_sources=retrieved,
            dropped_sources=dropped,
            suggestions=suggestions,
            latency=Latency(retrieval_ms=retrieval_ms, total_ms=_ms_since(started)),
        )


def _ms_since(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 1)
