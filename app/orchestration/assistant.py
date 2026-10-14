"""The investigation loop: one assistant that decides which read-only tools to use.

The application, not the model, owns the session: the project path, the GitHub
repository and the mode are fixed per session and injected into tool calls. The
schemas shown to the model don't contain ``project_path`` or ``repo`` at all, and
calls that try to pass them are rejected.

The loop is bounded (tool calls, GitHub calls, iterations, per-tool timeout),
caches duplicate calls, turns tool failures into structured results the model
can react to, assigns citation ids (S# project, G# GitHub) to evidence, validates
the final answer's citations, and records a content-free trace.
"""

import copy
import json
import logging
import re
import time
from collections import deque
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.config import Settings
from app.integrations.github_client import resolve_repo
from app.integrations.github_errors import GitHubNotConfiguredError
from app.llm.base import ChatMessage, LLMToolsUnsupportedError, ToolCall, ToolCallingLLM, ToolSpec
from app.llm.factory import get_llm_provider
from app.orchestration.prompts import (
    FINAL_TURN_NOTE,
    ORCH_PROMPT_VERSION,
    build_system_prompt,
    build_user_message,
)
from app.orchestration.tool_backends import ToolBackend, ToolDefinition, ToolResult

logger = logging.getLogger(__name__)

Mode = Literal["auto", "project", "github"]

PROJECT_TOOLS = frozenset({"get_index_stats", "search_project", "read_project_file", "list_project_files"})
GITHUB_TOOLS = frozenset({
    "get_github_repository", "search_github_issues", "get_github_issue",
    "list_github_pull_requests", "get_github_pull_request",
})
INJECTED_ARGS = frozenset({"project_path", "repo"})
MAX_RESULT_CHARS = 20_000  # tool result text sent to the model
MEMORY_ANSWER_CHARS = 1500  # how much of a previous answer is kept as context
_CITATION_GROUP = re.compile(r"\[\s*([SG]\d+(?:\s*[,;]\s*[SG]\d+)*)\s*\]")
_UNTRUSTED_WRAPPER = re.compile(r"^<untrusted-[a-z-]+ source=\"[^\"]*\">\n(.*)\n</untrusted-[a-z-]+>$", re.DOTALL)


class OrchestrationConfigError(ValueError):
    """The session can't be set up (e.g. GitHub mode without a repository)."""


class SessionConfig(BaseModel):
    """Fixed by the application for a whole session; never chosen by the model."""

    project_path: Path
    repo: str | None = None
    mode: Mode = "auto"


class EvidenceSource(BaseModel):
    cite_id: str  # "S1" or "G1"
    kind: Literal["project", "github"]
    label: str  # file path with location, or "owner/repo#123: title"
    tool: str
    file_path: str | None = None
    location: str | None = None
    score: float | None = None
    repo: str | None = None
    number: int | None = None
    url: str | None = None
    is_pull_request: bool | None = None
    cited: bool = False


class TraceStep(BaseModel):
    """One step of the investigation. Never contains file, issue or prompt content."""

    step: int
    iteration: int
    kind: Literal["llm", "tool"]
    name: str
    status: str  # llm: "tool_calls" | "answer"; tool: "ok" | "error" | "cached" | "rejected"
    duration_ms: float = 0.0
    args: dict[str, Any] = Field(default_factory=dict)  # sanitized: no injected values, short strings
    result_chars: int | None = None
    error_category: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    tool_calls_requested: int | None = None


class Latency(BaseModel):
    total_ms: float = 0.0
    llm_ms: float = 0.0
    tools_ms: float = 0.0


class InvestigationResult(BaseModel):
    question: str
    answer_text: str
    mode: Mode
    project_name: str
    repo: str | None
    project_sources: list[EvidenceSource] = Field(default_factory=list)
    github_sources: list[EvidenceSource] = Field(default_factory=list)
    invalid_citations: list[str] = Field(default_factory=list)
    tool_calls: int = 0  # executed (not cached, not rejected)
    cached_calls: int = 0
    rejected_calls: int = 0
    limits_hit: list[str] = Field(default_factory=list)
    github_disabled: bool = False
    provider: str
    model: str
    prompt_version: str = ORCH_PROMPT_VERSION
    llm_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency: Latency = Field(default_factory=Latency)
    trace: list[TraceStep] = Field(default_factory=list)

    @property
    def cited_sources(self) -> list[EvidenceSource]:
        return [s for s in self.project_sources + self.github_sources if s.cited]


def parse_citation_ids(text: str) -> list[str]:
    ids: list[str] = []
    for group in _CITATION_GROUP.findall(text):
        for cid in re.split(r"\s*[,;]\s*", group):
            if cid not in ids:
                ids.append(cid)
    return ids


def categorize_error(message: str) -> str:
    text = message.lower()
    for needle, category in (
        ("not indexed", "not_indexed"), ("not configured", "not_configured"), ("rate limit", "rate_limited"),
        ("timed out", "timeout"), ("not allowed", "access_denied"), ("not inside an allowed", "access_denied"),
        ("not found", "not_found"), ("validation", "invalid_arguments"), ("invalid", "invalid_arguments"),
        ("must ", "invalid_arguments"),
    ):
        if needle in text:
            return category
    return "tool_error"


def unwrap_untrusted(text: str | None) -> str:
    """Strip an <untrusted-...> wrapper for display (the content stays untrusted)."""
    if not text:
        return ""
    match = _UNTRUSTED_WRAPPER.match(text)
    return match.group(1) if match else text


class _Run:
    """Mutable state of one ask() call."""

    def __init__(self) -> None:
        self.trace: list[TraceStep] = []
        self.cache: dict[str, str] = {}
        self.executed = 0
        self.github_executed = 0
        self.cached = 0
        self.rejected = 0
        self.github_disabled = False
        self.limits_hit: list[str] = []
        self.sources: dict[str, EvidenceSource] = {}
        self.source_keys: dict[tuple[str, ...], str] = {}
        self.counters = {"S": 0, "G": 0}
        self.llm_calls = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.llm_ms = 0.0
        self.tools_ms = 0.0

    def add_trace(self, **fields: Any) -> None:
        self.trace.append(TraceStep(step=len(self.trace) + 1, **fields))

    def register(self, key: tuple[str, ...], prefix: str, make: Any) -> str:
        """Stable citation id per evidence item (same file range / same issue -> same id)."""
        if key in self.source_keys:
            return self.source_keys[key]
        self.counters[prefix] += 1
        cite_id = f"{prefix}{self.counters[prefix]}"
        self.source_keys[key] = cite_id
        self.sources[cite_id] = make(cite_id)
        return cite_id


class Investigator:
    """Answers questions with a bounded, read-only tool-use loop. Keeps short session memory."""

    def __init__(self, settings: Settings, llm: ToolCallingLLM, backend: ToolBackend, session: SessionConfig) -> None:
        self.settings = settings
        self.llm = llm
        self.backend = backend
        self.project = session.project_path.resolve()
        if not self.project.is_dir():
            raise OrchestrationConfigError("The project path is not a directory.")
        self.mode: Mode = session.mode
        self.repo = self._resolve_session_repo(session)
        self.history: deque[tuple[str, str]] = deque(maxlen=settings.orch_memory_turns)
        self._definitions = {d.name: d for d in backend.list_tools()}
        self.tools = self._visible_tools()
        self._allowed_args = {t.name: set(t.parameters.get("properties", {})) for t in self.tools}
        self._required_args = {t.name: set(t.parameters.get("required", [])) for t in self.tools}

    # ---- session setup ------------------------------------------------------------

    def _resolve_session_repo(self, session: SessionConfig) -> str | None:
        if session.mode == "project":
            return None
        candidate = session.repo or self.settings.github_default_repo
        if not candidate:
            if session.mode == "github":
                raise OrchestrationConfigError("GitHub mode needs a repository: pass --repo or set GITHUB_DEFAULT_REPO.")
            return None
        try:
            return resolve_repo(self.settings, candidate).full_name  # validates format + allowlist
        except GitHubNotConfiguredError as exc:
            raise OrchestrationConfigError(str(exc)) from None

    def _visible_tools(self) -> list[ToolSpec]:
        """Read-only tools for the mode, with session-owned arguments removed from their schemas."""
        allowed: set[str] = set()
        if self.mode in ("auto", "project"):
            allowed |= PROJECT_TOOLS
        if self.mode in ("auto", "github") and self.repo:
            allowed |= GITHUB_TOOLS
        specs = []
        for name in sorted(allowed):
            definition = self._definitions.get(name)
            if definition is None or not definition.read_only:
                continue
            schema = copy.deepcopy(definition.input_schema)
            props = schema.get("properties", {})
            for arg in INJECTED_ARGS:
                props.pop(arg, None)
            schema["required"] = [r for r in schema.get("required", []) if r not in INJECTED_ARGS]
            schema["additionalProperties"] = False
            specs.append(ToolSpec(name=name, description=definition.description, parameters=schema))
        return specs

    def _tool_groups(self) -> str:
        names = {t.name for t in self.tools}
        groups = []
        if names & PROJECT_TOOLS:
            groups.append("project search/read")
        if names & GITHUB_TOOLS:
            groups.append(f"GitHub issues/PRs for {self.repo}")
        return ", ".join(groups) or "none"

    # ---- public ---------------------------------------------------------------------

    def ask(self, question: str) -> InvestigationResult:
        if not question or not question.strip():
            raise OrchestrationConfigError("The question must not be empty.")
        started = time.perf_counter()
        run = _Run()
        system = build_system_prompt(
            self.project.name, self.repo, self.mode, self._tool_groups(), self.settings.orch_max_tool_calls,
            self.settings.orch_max_github_calls if self.repo else 0, self.settings.orch_max_iterations,
        )
        messages = [ChatMessage(role="user", content=build_user_message(question, list(self.history)))]
        final_text: str | None = None
        last_iteration = 0

        for iteration in range(1, self.settings.orch_max_iterations + 1):
            last_iteration = iteration
            response = self._llm_turn(run, iteration, system, messages, "auto")
            messages.append(response.message)
            if not response.tool_calls:
                final_text = response.text
                break
            for call in response.tool_calls:
                messages.append(self._handle_call(run, iteration, call))
            if run.executed >= self.settings.orch_max_tool_calls:
                self._limit(run, "max_tool_calls")
            if "max_tool_calls" in run.limits_hit:
                break
        else:
            self._limit(run, "max_iterations")

        if final_text is None:  # a limit was hit: one last turn, no tools
            messages.append(ChatMessage(role="user", content=FINAL_TURN_NOTE.format(reasons=", ".join(run.limits_hit))))
            response = self._llm_turn(run, last_iteration, system, messages, "none")
            final_text = response.text or "The investigation stopped before an answer was produced."

        return self._result(run, question, final_text, started)

    def reset(self) -> None:
        self.history.clear()

    # ---- loop pieces ---------------------------------------------------------------

    def _llm_turn(self, run: _Run, iteration: int, system: str, messages: list[ChatMessage], choice: str):  # type: ignore[no-untyped-def]
        t0 = time.perf_counter()
        response = self.llm.chat(system, messages, self.tools, tool_choice=choice)  # type: ignore[arg-type]
        elapsed = (time.perf_counter() - t0) * 1000
        run.llm_calls += 1
        run.llm_ms += elapsed
        run.input_tokens += response.input_tokens or 0
        run.output_tokens += response.output_tokens or 0
        run.add_trace(iteration=iteration, kind="llm", name=response.model,
                      status="tool_calls" if response.tool_calls else "answer", duration_ms=round(elapsed, 1),
                      input_tokens=response.input_tokens, output_tokens=response.output_tokens,
                      tool_calls_requested=len(response.tool_calls))
        return response

    def _handle_call(self, run: _Run, iteration: int, call: ToolCall) -> ChatMessage:
        def reply(payload: dict[str, Any], is_error: bool) -> ChatMessage:
            return ChatMessage(role="tool", tool_call_id=call.id, content=json.dumps(payload), is_error=is_error)

        def reject(message: str, category: str) -> ChatMessage:
            run.rejected += 1
            run.add_trace(iteration=iteration, kind="tool", name=call.name, status="rejected",
                          args=self._sanitize(call.arguments), error_category=category)
            return reply({"ok": False, "error": message, "category": category}, True)

        # 1. Validate against the visible tools and their (stripped) schemas.
        if call.name not in self._allowed_args:
            available = ", ".join(sorted(self._allowed_args)) or "none"
            return reject(f"Unknown or unavailable tool '{call.name}'. Available tools: {available}.", "unknown_tool")
        if call.parse_error:
            return reject(f"Invalid arguments: {call.parse_error}.", "invalid_arguments")
        extra = set(call.arguments) - self._allowed_args[call.name]
        if extra:
            return reject(f"Arguments not allowed: {', '.join(sorted(extra))}. Project and repository are "
                          "fixed by the session.", "invalid_arguments")
        missing = self._required_args[call.name] - set(call.arguments)
        if missing:
            return reject(f"Missing required arguments: {', '.join(sorted(missing))}.", "invalid_arguments")

        # 2. Inject session-owned arguments (the model can't choose them).
        args = dict(call.arguments)
        schema_props = self._definitions[call.name].input_schema.get("properties", {})
        if "project_path" in schema_props:
            args["project_path"] = str(self.project)
        if "repo" in schema_props and self.repo:
            args["repo"] = self.repo

        # 3. Duplicate calls return the cached result without running again.
        key = f"{call.name}:{json.dumps(args, sort_keys=True)}"
        if key in run.cache:
            run.cached += 1
            run.add_trace(iteration=iteration, kind="tool", name=call.name, status="cached",
                          args=self._sanitize(call.arguments), result_chars=len(run.cache[key]))
            return ChatMessage(role="tool", tool_call_id=call.id, content=run.cache[key])

        # 4. Limits.
        is_github = call.name in GITHUB_TOOLS
        if run.executed >= self.settings.orch_max_tool_calls:
            self._limit(run, "max_tool_calls")
            return reject("Tool call limit reached. Answer with the evidence you have.", "limit_reached")
        if is_github and run.github_disabled:
            return reject("GitHub calls are disabled for this question after a rate limit.", "github_disabled")
        if is_github and run.github_executed >= self.settings.orch_max_github_calls:
            self._limit(run, "max_github_calls")
            return reject("GitHub call limit reached. Use the GitHub results you already have.", "limit_reached")

        # 5. Execute.
        t0 = time.perf_counter()
        result = self.backend.call_tool(call.name, args, self.settings.orch_tool_timeout_seconds)
        elapsed = (time.perf_counter() - t0) * 1000
        run.tools_ms += elapsed
        run.executed += 1
        run.github_executed += int(is_github)

        if result.ok:
            payload = {"ok": True, "result": self._annotate(run, call.name, result)}
            content = json.dumps(payload, default=str)
            if len(content) > MAX_RESULT_CHARS:  # keep it valid JSON, flagged as cut
                content = json.dumps({"ok": True, "truncated_by_orchestrator": True,
                                      "result_json_prefix": content[:MAX_RESULT_CHARS]})
            run.cache[key] = content
            run.add_trace(iteration=iteration, kind="tool", name=call.name, status="ok",
                          args=self._sanitize(call.arguments), duration_ms=round(elapsed, 1),
                          result_chars=len(content))
            return ChatMessage(role="tool", tool_call_id=call.id, content=content)

        category = categorize_error(result.error or "")
        if is_github and category == "rate_limited":
            run.github_disabled = True
        content = json.dumps({"ok": False, "error": result.error, "category": category})
        run.add_trace(iteration=iteration, kind="tool", name=call.name, status="error",
                      args=self._sanitize(call.arguments), duration_ms=round(elapsed, 1),
                      result_chars=len(content), error_category=category)
        return ChatMessage(role="tool", tool_call_id=call.id, content=content, is_error=True)

    @staticmethod
    def _limit(run: _Run, name: str) -> None:
        if name not in run.limits_hit:
            run.limits_hit.append(name)

    @staticmethod
    def _sanitize(arguments: dict[str, Any]) -> dict[str, Any]:
        """Arguments as the model sent them, minus session values; long strings shortened."""
        clean: dict[str, Any] = {}
        for k, v in arguments.items():
            if k in INJECTED_ARGS:
                clean[k] = "<session>"
            elif isinstance(v, str):
                clean[k] = v if len(v) <= 100 else v[:100] + "…"
            elif isinstance(v, (int, float, bool)) or v is None:
                clean[k] = v
            elif isinstance(v, list):
                clean[k] = [str(i)[:40] for i in v[:10]]
            else:
                clean[k] = f"<{type(v).__name__}>"
        return clean

    # ---- evidence and citations ---------------------------------------------------------

    def _annotate(self, run: _Run, tool: str, result: ToolResult) -> dict[str, Any]:
        """Add "cite" ids to evidence items and register them as sources."""
        data = copy.deepcopy(result.data or {})

        def project(file_path: str, location: str, score: float | None) -> str:
            return run.register(("S", file_path, location), "S", lambda cid: EvidenceSource(
                cite_id=cid, kind="project", label=f"{file_path} ({location})", tool=tool,
                file_path=file_path, location=location, score=score))

        def github(item: dict[str, Any], repo: str, is_pr: bool | None) -> str:
            number = int(item["number"])
            title = unwrap_untrusted(item.get("title"))
            return run.register(("G", repo.lower(), str(number)), "G", lambda cid: EvidenceSource(
                cite_id=cid, kind="github", label=f"{repo}#{number}: {title}"[:200], tool=tool,
                repo=repo, number=number, url=item.get("html_url"), is_pull_request=is_pr))

        if tool == "search_project":
            for hit in data.get("hits", []):
                hit["cite"] = project(hit["file_path"], hit.get("location", ""), hit.get("score"))
        elif tool == "read_project_file":
            location = f"lines {data.get('start_line')}-{data.get('end_line')}"
            data["cite"] = project(data["file_path"], location, None)
        elif tool == "search_github_issues":
            for item in data.get("items", []):
                item["cite"] = github(item, data.get("repo", self.repo or ""), item.get("is_pull_request"))
        elif tool == "list_github_pull_requests":
            for item in data.get("items", []):
                item["cite"] = github(item, data.get("repo", self.repo or ""), True)
        elif tool == "get_github_issue":
            data["cite"] = github(data, data.get("repo", self.repo or ""), data.get("is_pull_request"))
        elif tool == "get_github_pull_request":
            data["cite"] = github(data, data.get("repo", self.repo or ""), True)
        return data

    def _result(self, run: _Run, question: str, answer: str, started: float) -> InvestigationResult:
        cited = parse_citation_ids(answer)
        invalid = [c for c in cited if c not in run.sources]
        for cid in cited:
            if cid in run.sources:
                run.sources[cid].cited = True
        if invalid:
            logger.warning("Answer contains %d invalid citation(s)", len(invalid))
        self.history.append((question.strip(), answer[:MEMORY_ANSWER_CHARS]))
        sources = list(run.sources.values())
        return InvestigationResult(
            question=question, answer_text=answer, mode=self.mode, project_name=self.project.name, repo=self.repo,
            project_sources=[s for s in sources if s.kind == "project"],
            github_sources=[s for s in sources if s.kind == "github"],
            invalid_citations=invalid, tool_calls=run.executed, cached_calls=run.cached,
            rejected_calls=run.rejected, limits_hit=run.limits_hit, github_disabled=run.github_disabled,
            provider=self.llm.provider, model=self.llm.model, llm_calls=run.llm_calls,
            input_tokens=run.input_tokens, output_tokens=run.output_tokens,
            latency=Latency(total_ms=round((time.perf_counter() - started) * 1000, 1),
                            llm_ms=round(run.llm_ms, 1), tools_ms=round(run.tools_ms, 1)),
            trace=run.trace,
        )


def build_tool_llm(settings: Settings) -> ToolCallingLLM:
    """The configured LLM, which must support tool calling."""
    llm = get_llm_provider(settings)
    if not callable(getattr(llm, "chat", None)):
        raise LLMToolsUnsupportedError(f"LLM provider '{settings.llm_provider}' does not support tool calling.")
    return llm  # type: ignore[return-value]
