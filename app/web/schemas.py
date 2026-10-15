"""Request/response models for the web API and helpers that shape results for the UI."""

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.actions.issue_draft import IssueDraft
from app.config import Settings
from app.ingestion.file_loader import FileLoader
from app.orchestration.assistant import EvidenceSource, InvestigationResult, unwrap_untrusted

Mode = Literal["auto", "project", "github"]


# ---- requests ------------------------------------------------------------------------


class IndexRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class AskRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=4096)
    question: str = Field(min_length=1, max_length=4000)
    top_k: int | None = Field(default=None, ge=1, le=20)
    file_types: list[str] | None = None


class ConversationCreate(BaseModel):
    path: str = Field(min_length=1, max_length=4096)
    repo: str | None = Field(default=None, max_length=140)
    mode: Mode = "auto"


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    mode: Mode | None = None


class HistorySettings(BaseModel):
    save_history: bool


class InvestigateRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=4096)  # must match the conversation's project
    question: str = Field(min_length=1, max_length=4000)
    mode: Mode | None = None  # one-off override (e.g. a GitHub-only follow-up); defaults to the conversation's


class DraftCreate(BaseModel):
    turn_id: str
    repo: str | None = None  # destination; defaults to the only write-allowed repo


class DraftEdit(BaseModel):
    title: str = Field(max_length=1000)
    body: str = Field(max_length=70_000)
    labels: list[str] = Field(default_factory=list, max_length=20)


class DraftApprove(BaseModel):
    draft_hash: str = Field(min_length=64, max_length=64)  # the exact content the human reviewed
    typed_repo_confirmation: str = Field(max_length=200)
    confirm_similar: bool = False


# ---- shaping results -------------------------------------------------------------------


_LINE_PREFIX = re.compile(r"^\s*(\d+) \| ?", re.MULTILINE)


class SourceOut(BaseModel):
    cite_id: str
    kind: str
    label: str
    cited: bool
    file_path: str | None = None
    location: str | None = None
    score: float | None = None
    start_line: int | None = None
    end_line: int | None = None
    language: str | None = None
    excerpt: str | None = None  # untrusted content, unwrapped for display (the UI marks it as quoted)
    repo: str | None = None
    number: int | None = None
    url: str | None = None
    is_pull_request: bool | None = None
    state: str | None = None
    labels: list[str] = Field(default_factory=list)
    title: str | None = None


LANGUAGES = {".py": "python", ".md": "markdown", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
             ".ts": "typescript", ".tsx": "typescript", ".js": "javascript", ".toml": "ini", ".txt": "plaintext"}


def line_info(settings: Settings, project: Path, file_path: str | None,
              location: str | None) -> tuple[int | None, int | None, bool]:
    """First and last line of a source, and whether it starts mid-line.

    Parsed from "lines a-b", or converted from "chars a-b" by reading the file through
    the shared path rules (FileLoader.check_path).
    """
    if not location:
        return None, None, False
    lines = re.match(r"lines (\d+)-(\d+)", location)
    if lines:
        return int(lines.group(1)), int(lines.group(2)), False
    chars = re.match(r"chars (\d+)-(\d+)", location)
    if not chars or not file_path:
        return None, None, False
    loader = FileLoader(settings.model_copy(update={"project_root": project}), root=project)
    if loader.check_path(file_path)[0] is not None:
        return None, None, False
    try:
        text = (project / file_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None, None, False
    start, end = int(chars.group(1)), int(chars.group(2))
    first = text[:start].count("\n") + 1
    last = text[:max(start, end - 1)].count("\n") + 1  # end is exclusive
    partial = 0 < start <= len(text) and text[start - 1] != "\n"
    return first, max(first, last), partial


def line_range(settings: Settings, project: Path, file_path: str | None,
               location: str | None) -> tuple[int | None, int | None]:
    start, end, _ = line_info(settings, project, file_path, location)
    return start, end


def trim_partial_line(excerpt: str | None, start: int | None, end: int | None,
                      partial: bool) -> tuple[str | None, int | None]:
    """Chunks can start mid-line: for display, drop that fragment and start at the next full line."""
    if not partial or not excerpt or start is None or "\n" not in excerpt or (end is not None and start >= end):
        return excerpt, start
    return excerpt.split("\n", 1)[1], start + 1


def source_out(src: EvidenceSource, settings: Settings, project: Path | None) -> SourceOut:
    excerpt = unwrap_untrusted(src.excerpt) if src.excerpt else None
    if excerpt and src.kind == "project" and src.location and src.location.startswith("lines"):
        excerpt = _LINE_PREFIX.sub("", excerpt)  # the read tool prefixes line numbers; the UI adds its own
    title = None
    if src.kind == "github" and ": " in src.label:
        title = unwrap_untrusted(src.label.split(": ", 1)[1])
    start, end, partial = line_info(settings, project, src.file_path, src.location) \
        if project and src.kind == "project" else (None, None, False)
    excerpt, start = trim_partial_line(excerpt, start, end, partial)
    return SourceOut(
        cite_id=src.cite_id, kind=src.kind, label=src.label, cited=src.cited, file_path=src.file_path,
        location=src.location, score=src.score, start_line=start, end_line=end,
        language=LANGUAGES.get(Path(src.file_path).suffix.lower()) if src.file_path else None,
        excerpt=excerpt, repo=src.repo, number=src.number, url=src.url, is_pull_request=src.is_pull_request,
        state=src.state, labels=src.labels, title=title,
    )


def parse_answer(text: str) -> dict[str, Any]:
    """Split a Markdown answer into ``## sections`` and ranked possible causes."""
    sections: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    preamble: list[str] = []
    for line in text.splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = {"heading": heading.group(1), "markdown": ""}
            sections.append(current)
        elif current is None:
            preamble.append(line)
        else:
            current["markdown"] += line + "\n"
    if "".join(preamble).strip():
        sections.insert(0, {"heading": "", "markdown": "\n".join(preamble)})
    for section in sections:
        section["markdown"] = section["markdown"].strip()

    causes: list[dict[str, Any]] = []
    for section in sections:
        if section["heading"].lower().startswith("possible causes"):
            for match in re.finditer(r"(?ms)^\s*(\d+)\.\s+(.*?)(?=^\s*\d+\.\s|\Z)", section["markdown"]):
                body = match.group(2).strip()
                lowered = body.lower()
                kind = ("evidence" if "supported by evidence" in lowered else
                        "hypothesis" if "hypothesis" in lowered else "unlabeled")
                causes.append({"rank": int(match.group(1)), "kind": kind, "markdown": body})
    return {"sections": sections, "causes": causes}


def investigation_out(result: InvestigationResult, turn_id: str, project: Path, settings: Settings) -> dict[str, Any]:
    """The final ``answer`` event payload."""
    return {
        "turn_id": turn_id,
        "question": result.question,
        "answer_markdown": result.answer_text,
        **parse_answer(result.answer_text),
        "project_sources": [source_out(s, settings, project).model_dump() for s in result.project_sources],
        "github_sources": [source_out(s, settings, None).model_dump() for s in result.github_sources],
        "invalid_citations": result.invalid_citations,
        "limits_hit": result.limits_hit,
        "github_disabled": result.github_disabled,
        "totals": {
            "tool_calls": result.tool_calls, "cached_calls": result.cached_calls,
            "rejected_calls": result.rejected_calls, "llm_calls": result.llm_calls,
            "input_tokens": result.input_tokens, "output_tokens": result.output_tokens,
            "latency_ms": result.latency.total_ms, "llm_ms": result.latency.llm_ms,
            "tools_ms": result.latency.tools_ms,
        },
        "trace": [t.model_dump(exclude_none=True) for t in result.trace],
        "model": result.model, "provider": result.provider, "prompt_version": result.prompt_version,
        "mode": result.mode, "repo": result.repo, "has_model_draft": result.issue_draft is not None,
    }


def draft_out(draft: IssueDraft, attempted: bool, result: Any = None) -> dict[str, Any]:
    return {
        "id": draft.draft_id, "repo": draft.repo, "title": draft.title, "body": draft.body,
        "labels": draft.labels, "draft_hash": draft.draft_hash, "errors": draft.errors,
        "warnings": draft.warnings, "attempted": attempted,
        "result": result.model_dump(mode="json") if result is not None else None,
    }
