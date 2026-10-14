"""JSON + SSE API under /api. A thin layer over the existing app code: no business logic here.

Long-running work (indexing, quick answers, investigations) runs in a worker
thread; its progress events go through a queue to an async SSE generator. If the
client disconnects (e.g. the Stop button), a cancel flag stops the investigation
at its next event.

Issue creation goes only through the existing Phase 7 ``IssueApprovalService``:
the client must send the exact ``draft_hash`` it reviewed and the typed repository
name; approvals stay single-use, expiring and hash-bound; one attempt per draft.
"""

import asyncio
import logging
import platform
import queue
import secrets
import threading
import time
from collections.abc import AsyncIterator, Callable
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.actions.approval import ApprovalError
from app.config import Settings
from app.integrations.github_client import allowed_repos, resolve_repo
from app.integrations.github_errors import (
    GitHubError,
    GitHubNotConfiguredError,
    RateLimitError,
    RepoNotAllowedError,
)
from app.integrations.github_write_client import write_allowed_repos, write_configured
from app.llm.base import (
    LLMAuthError,
    LLMBadRequestError,
    LLMConfigError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMToolsUnsupportedError,
)
from app.mcp_server.security import SecurityError, ServerConfigError
from app.orchestration.assistant import InvestigationCancelled, OrchestrationConfigError, draft_from_answer
from app.rag.answer_generator import AnswerGenerator
from app.retrieval.indexer import build_index, index_stats
from app.retrieval.retriever import InvalidQueryError
from app.retrieval.vector_store import EmbeddingModelMismatchError, ProjectNotIndexedError, VectorStoreError
from app.web import schemas
from app.web.security import validate_project
from app.web.sessions import SessionManager

logger = logging.getLogger(__name__)

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "chroma_db", "audit"}


# ---- errors -------------------------------------------------------------------------------


class ApiError(Exception):
    def __init__(self, status: int, category: str, message: str, hint: str | None = None) -> None:
        super().__init__(message)
        self.status, self.category, self.message, self.hint = status, category, message, hint

    def payload(self) -> dict[str, Any]:
        return {"error": {"category": self.category, "message": self.message, "hint": self.hint}}


def to_api_error(exc: Exception) -> ApiError:
    """Known failures -> safe, friendly errors. Messages are written by this app, never raw internals."""
    if isinstance(exc, ApiError):
        return exc
    if isinstance(exc, ProjectNotIndexedError):
        return ApiError(409, "not_indexed", "This project is not indexed yet.", "Click Index in the sidebar first.")
    if isinstance(exc, EmbeddingModelMismatchError):
        return ApiError(409, "model_mismatch", "The index was built with a different embedding model.",
                        "Rebuild the index from the sidebar.")
    if isinstance(exc, (SecurityError, ServerConfigError)):
        return ApiError(403, "access_denied", str(exc), "Projects must be inside MCP_ALLOWED_PROJECT_ROOTS.")
    if isinstance(exc, LLMConfigError):
        return ApiError(503, "llm_not_configured", str(exc), "Set LLM_PROVIDER, LLM_MODEL and the API key in .env.")
    if isinstance(exc, LLMToolsUnsupportedError):
        return ApiError(400, "llm_no_tools", str(exc), "Choose a model that supports tool calling.")
    if isinstance(exc, LLMAuthError):
        return ApiError(502, "llm_auth", str(exc), "Check the API key in .env.")
    if isinstance(exc, LLMBadRequestError) and "credits" in str(exc).lower():
        return ApiError(402, "llm_credits", str(exc), "Add credits to the LLM provider account.")
    if isinstance(exc, LLMRateLimitError):
        return ApiError(429, "llm_rate_limited", str(exc), "Wait a minute and try again.")
    if isinstance(exc, LLMTimeoutError):
        return ApiError(504, "llm_timeout", str(exc), "Try again, or raise LLM_TIMEOUT_SECONDS.")
    if isinstance(exc, LLMError):
        return ApiError(502, "llm_error", str(exc), None)
    if isinstance(exc, RateLimitError):
        return ApiError(429, "github_rate_limited", str(exc), "Set GITHUB_TOKEN for a higher limit, or wait.")
    if isinstance(exc, (GitHubNotConfiguredError, RepoNotAllowedError)):
        return ApiError(400, "github_not_allowed", str(exc), "Check GITHUB_ALLOWED_REPOS / GITHUB_WRITE_* in .env.")
    if isinstance(exc, GitHubError):
        return ApiError(502, "github_error", str(exc), None)
    if isinstance(exc, ApprovalError):
        return ApiError(409, "approval_refused", str(exc), None)
    if isinstance(exc, (InvalidQueryError, OrchestrationConfigError)):
        return ApiError(400, "invalid_request", str(exc), None)
    if isinstance(exc, VectorStoreError):
        return ApiError(500, "index_error", "The vector index could not be read.", "Try rebuilding the index.")
    logger.error("Unhandled %s in web API", type(exc).__name__)
    return ApiError(500, "internal", "Something went wrong.", "Check the server log.")


def http_error(exc: Exception) -> HTTPException:
    err = to_api_error(exc)
    return HTTPException(status_code=err.status, detail=err.payload()["error"])


# ---- streaming helper ---------------------------------------------------------------------------


SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
KEEPALIVE_SECONDS = 15.0


def sse(event: str, data: Any) -> bytes:
    """One Server-Sent Event: ``event:`` name + JSON ``data:``."""
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n".encode()


def stream(request: Request, work: Callable[[Callable[[str, Any], None], threading.Event], Any],
           delay: float = 0.0) -> StreamingResponse:
    """Run ``work(emit, cancel)`` in a thread and stream what it emits as SSE events."""
    events: queue.Queue[tuple[str, Any] | None] = queue.Queue()
    cancel = threading.Event()

    def emit(name: str, data: Any) -> None:
        if cancel.is_set():
            raise InvestigationCancelled()
        events.put((name, data))
        if delay:
            time.sleep(delay)

    def run() -> None:
        try:
            work(emit, cancel)
        except InvestigationCancelled:
            events.put(("cancelled", {}))
        except Exception as exc:  # noqa: BLE001 - mapped to a safe error event
            events.put(("error", to_api_error(exc).payload()["error"]))
        finally:
            events.put(None)

    async def generate() -> AsyncIterator[bytes]:
        threading.Thread(target=run, daemon=True).start()
        idle_since = time.monotonic()
        try:
            while True:
                try:
                    item = events.get_nowait()
                except queue.Empty:
                    if await request.is_disconnected():
                        cancel.set()
                        return
                    if time.monotonic() - idle_since > KEEPALIVE_SECONDS:
                        idle_since = time.monotonic()
                        yield b": ping\n\n"  # keeps proxies from closing a quiet stream
                    await asyncio.sleep(0.03)
                    continue
                if item is None:
                    return
                idle_since = time.monotonic()
                yield sse(*item)
        finally:
            cancel.set()

    return StreamingResponse(generate(), media_type="text/event-stream", headers=SSE_HEADERS)


# ---- routes ------------------------------------------------------------------------------------


def build_router(sessions: SessionManager) -> APIRouter:
    runtime = sessions.runtime
    settings: Settings = runtime.settings
    router = APIRouter(prefix="/api")
    indexed_paths: set[Path] = set()

    def project_for(path: str) -> Path:
        try:
            return validate_project(settings, path)
        except Exception as exc:  # noqa: BLE001
            raise http_error(exc) from None

    def settings_for(project: Path) -> Settings:
        return settings.model_copy(update={"project_root": project})

    @router.get("/status")
    def status() -> dict[str, Any]:
        def pkg(name: str) -> str | None:
            try:
                return version(name)
            except PackageNotFoundError:
                return None

        try:
            read_repos = [r.full_name for r in allowed_repos(settings)]
        except GitHubError:
            read_repos = []
        return {
            "demo": runtime.demo,
            "llm": {"provider": settings.llm_provider, "model": settings.llm_model},
            "embedding_model": settings.embedding_model if settings.embedding_provider != "fake" else "fake (demo)",
            "github": {
                "default_repo": settings.github_default_repo, "read_repos": read_repos,
                "authenticated_reads": settings.github_token is not None,
                "write_enabled": write_configured(settings),
                "write_repos": [r.full_name for r in write_allowed_repos(settings)] if write_configured(settings) else [],
            },
            "limits": {"max_tool_calls": settings.orch_max_tool_calls, "max_iterations": settings.orch_max_iterations,
                       "max_github_calls": settings.orch_max_github_calls},
            "allowed_roots": [str(Path(p).resolve()) for p in settings.mcp_allowed_roots],
            "versions": {"app": pkg("ai-developer-assistant"), "python": platform.python_version(),
                         "fastapi": pkg("fastapi")},
        }

    @router.get("/projects")
    def projects() -> dict[str, Any]:
        """Indexed projects under the allowed roots (a few levels deep) plus ones indexed in this run."""
        found: dict[Path, dict[str, Any]] = {}
        candidates: list[Path] = []
        for root in settings.mcp_allowed_roots:
            root = root.resolve()
            candidates.append(root)
            level = [root]
            for _ in range(3):
                nxt = []
                for directory in level:
                    try:
                        children = [c for c in directory.iterdir() if c.is_dir() and not c.is_symlink()
                                    and c.name not in _SKIP_DIRS and not c.name.startswith(".")]
                    except OSError:
                        continue
                    nxt += children
                candidates += nxt
                level = nxt
        candidates += list(indexed_paths)
        demo_default = settings.project_root.resolve() if runtime.demo else None
        for path in candidates:
            if path in found:
                continue
            try:
                stats = index_stats(settings, path)
            except ProjectNotIndexedError:
                if path == demo_default:
                    found[path] = {"path": str(path), "name": path.name, "indexed": False, "chunks": 0,
                                   "files": 0, "model": None}
                continue
            except Exception:  # noqa: BLE001
                continue
            found[path] = {"path": str(path), "name": path.name, "indexed": True, "chunks": stats.chunk_count,
                           "files": len(stats.files), "model": stats.model_name, "file_list": stats.files[:500]}
        return {"projects": sorted(found.values(), key=lambda p: (not p["indexed"], p["name"]))}

    @router.post("/index")
    def index(body: schemas.IndexRequest, request: Request) -> StreamingResponse:
        project = project_for(body.path)

        def work(emit: Callable[[str, Any], None], cancel: threading.Event) -> None:
            emit("started", {"path": str(project), "name": project.name})
            report = build_index(project, rebuild=True, settings=settings_for(project), provider=runtime.embeddings,
                                 progress=lambda e: emit("progress", e))
            indexed_paths.add(project)
            emit("done", {"files_indexed": report.files_indexed, "chunks_indexed": report.chunks_indexed,
                          "files_skipped": report.files_skipped, "chunks_per_file_type": report.chunks_per_file_type,
                          "duration_seconds": report.duration_seconds, "model": report.model_name})

        return stream(request, work, delay=runtime.index_event_delay)

    @router.post("/ask")
    def ask(body: schemas.AskRequest, request: Request) -> StreamingResponse:
        project = project_for(body.path)

        def work(emit: Callable[[str, Any], None], cancel: threading.Event) -> None:
            emit("status", {"stage": "retrieving"})
            generator = AnswerGenerator(settings_for(project), llm=runtime.llm, embedding_provider=runtime.embeddings)
            answer = generator.answer(body.question, project, top_k=body.top_k, file_types=body.file_types)
            emit("status", {"stage": "done"})
            emit("answer", {
                "answer_markdown": answer.answer_text, **schemas.parse_answer(answer.answer_text),
                "insufficient_evidence": answer.insufficient_evidence, "invalid_citations": answer.invalid_citations,
                "sources": [s.model_dump() for s in answer.context_sources],
                "cited": [s.source_id for s in answer.cited_sources], "suggestions": answer.suggestions,
                "model": answer.model, "input_tokens": answer.input_tokens, "output_tokens": answer.output_tokens,
                "latency": answer.latency.model_dump(), "prompt_version": answer.prompt_version,
            })

        return stream(request, work)

    # ---- sessions --------------------------------------------------------------------------

    @router.post("/sessions")
    def create_session(body: schemas.SessionCreate) -> dict[str, Any]:
        project = project_for(body.path)
        try:
            if body.repo:
                resolve_repo(settings, body.repo)  # validated against the read allowlist
            session = sessions.create(project, body.repo, body.mode)
        except Exception as exc:  # noqa: BLE001
            raise http_error(exc) from None
        return session_out(session)

    def session_out(session: Any) -> dict[str, Any]:
        return {"id": session.id, "path": str(session.project), "name": session.project.name, "repo": session.repo,
                "mode": session.mode, "turns": list(session.turns),
                "memory_turns": len(session.investigator.history),
                "tools": [t.name for t in session.investigator.tools]}

    def get_session(session_id: str) -> Any:
        session = sessions.get(session_id)
        if session is None:
            raise HTTPException(404, detail={"category": "not_found", "message": "Session not found.",
                                             "hint": "Start a new conversation."})
        return session

    @router.get("/sessions/{session_id}")
    def read_session(session_id: str) -> dict[str, Any]:
        return session_out(get_session(session_id))

    @router.delete("/sessions/{session_id}")
    def delete_session(session_id: str) -> dict[str, Any]:
        get_session(session_id)
        sessions.delete(session_id)
        return {"deleted": True}

    @router.post("/investigate")
    def investigate(body: schemas.InvestigateRequest, request: Request) -> StreamingResponse:
        session = get_session(body.session_id)
        if not session.lock.acquire(blocking=False):
            raise HTTPException(409, detail={"category": "busy", "message": "An investigation is already running.",
                                             "hint": "Wait for it to finish or press Stop."})

        def work(emit: Callable[[str, Any], None], cancel: threading.Event) -> None:
            try:
                emit("started", {"question": body.question, "mode": session.mode, "repo": session.repo})

                def on_event(event: dict[str, Any]) -> None:
                    emit(event.pop("type"), event)

                result = session.investigator.ask(body.question, on_event=on_event)
                turn_id = secrets.token_urlsafe(8)
                session.turns[turn_id] = result
                emit("answer", schemas.investigation_out(result, turn_id, session.project, settings))
                emit("done", {"turn_id": turn_id})
            finally:
                session.lock.release()

        return stream(request, work)

    # ---- drafts (human approval; the LLM has no path here) --------------------------------------

    def find_draft(draft_id: str) -> tuple[Any, Any]:
        found = sessions.find_draft(draft_id)
        if found is None:
            raise HTTPException(404, detail={"category": "not_found", "message": "Draft not found.", "hint": None})
        return found

    def write_hint() -> str:
        return ("Set GITHUB_WRITE_ENABLED=true, GITHUB_WRITE_TOKEN and GITHUB_WRITE_ALLOWED_REPOS in .env "
                "(see docs/approval.md), then restart.")

    @router.post("/sessions/{session_id}/drafts")
    def prepare_draft(session_id: str, body: schemas.DraftCreate) -> dict[str, Any]:
        session = get_session(session_id)
        if not write_configured(settings):
            raise HTTPException(400, detail={"category": "write_disabled",
                                             "message": "GitHub issue creation is disabled.", "hint": write_hint()})
        result = session.turns.get(body.turn_id)
        if result is None:
            raise HTTPException(404, detail={"category": "not_found", "message": "Answer not found.", "hint": None})
        repos = [r.full_name for r in write_allowed_repos(settings)]
        repo = body.repo or (repos[0] if len(repos) == 1 else None)
        if repo is None:
            raise HTTPException(400, detail={"category": "invalid_request",
                                             "message": "Choose a destination repository.", "hint": None})
        draft = result.issue_draft if result.issue_draft and result.issue_draft.repo == repo else \
            draft_from_answer(settings, result, repo, session.project)
        draft = session.approvals.add_draft(draft.model_copy(deep=True))
        return schemas.draft_out(draft, attempted=False)

    @router.put("/drafts/{draft_id}")
    def edit_draft(draft_id: str, body: schemas.DraftEdit) -> dict[str, Any]:
        session, _ = find_draft(draft_id)
        try:
            draft = session.approvals.edit_draft(draft_id, title=body.title, body=body.body, labels=body.labels)
        except Exception as exc:  # noqa: BLE001
            raise http_error(exc) from None
        return schemas.draft_out(draft, attempted=draft_id in session.approvals.attempted)

    @router.get("/drafts/{draft_id}")
    def read_draft(draft_id: str) -> dict[str, Any]:
        session, draft = find_draft(draft_id)
        return schemas.draft_out(draft, draft_id in session.approvals.attempted, session.approvals.results.get(draft_id))

    @router.get("/drafts/{draft_id}/similar")
    def similar(draft_id: str) -> dict[str, Any]:
        session, draft = find_draft(draft_id)
        if not draft.valid:
            return {"similar": [], "checked": False}
        try:
            return {"similar": [s.model_dump() for s in session.approvals.find_similar(draft)], "checked": True}
        except GitHubError as exc:
            return {"similar": [], "checked": False, "error": to_api_error(exc).message}

    @router.post("/drafts/{draft_id}/approve")
    def approve(draft_id: str, body: schemas.DraftApprove) -> dict[str, Any]:
        session, draft = find_draft(draft_id)
        if body.draft_hash != draft.draft_hash:
            raise HTTPException(409, detail={"category": "draft_changed",
                                             "message": "The draft changed since you reviewed it. Review it again.",
                                             "hint": None})
        try:
            approval = session.approvals.approve(draft_id, body.typed_repo_confirmation)
            result = session.approvals.submit(approval.approval_id, confirm_similar=body.confirm_similar)
        except Exception as exc:  # noqa: BLE001
            raise http_error(exc) from None
        return {"result": result.model_dump(mode="json"),
                "draft": schemas.draft_out(draft, draft_id in session.approvals.attempted, result)}

    @router.post("/drafts/{draft_id}/cancel")
    def cancel(draft_id: str) -> dict[str, Any]:
        session, draft = find_draft(draft_id)
        session.approvals.cancel(draft_id)
        return {"cancelled": True}

    return router

