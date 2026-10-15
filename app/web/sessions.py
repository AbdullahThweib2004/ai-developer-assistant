"""In-memory web sessions: one investigator (with its conversation memory), one tool
backend and one approval service per session.

A session's project, repository and mode are fixed when it's created (the model
can never change them). Changing any of them in the UI creates a new session.
"""

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.actions.approval import IssueApprovalService
from app.actions.issue_draft import IssueDraft
from app.config import Settings
from app.llm.base import ToolCallingLLM
from app.mcp_server.tools.github_tools import GitHubTools
from app.orchestration.assistant import InvestigationResult, Investigator, Mode, SessionConfig, build_tool_llm
from app.orchestration.tool_backends import MCPClientBackend, ToolBackend
from app.retrieval.embeddings import EmbeddingProvider, get_embedding_provider

MAX_SESSIONS = 20


@dataclass
class Runtime:
    """Everything the API needs that differs between real mode and --demo mode."""

    settings: Settings
    make_llm: Callable[[], ToolCallingLLM]
    make_backend: Callable[[Path], ToolBackend]
    make_approvals: Callable[[Path], IssueApprovalService]
    make_embeddings: Callable[[], EmbeddingProvider]
    make_github: Callable[[], GitHubTools] | None = None  # read-only explore pages
    demo: bool = False
    index_event_delay: float = 0.0  # demo mode slows progress events down so they're visible
    _llm: ToolCallingLLM | None = None
    _embeddings: EmbeddingProvider | None = None
    _github: GitHubTools | None = None

    @property
    def llm(self) -> ToolCallingLLM:
        if self._llm is None:
            self._llm = self.make_llm()  # raises a clear LLM config error if not configured
        return self._llm

    @property
    def embeddings(self) -> EmbeddingProvider:
        if self._embeddings is None:
            self._embeddings = self.make_embeddings()  # one model per process, loaded on first use
        return self._embeddings

    @property
    def github(self) -> GitHubTools:
        if self._github is None:
            self._github = self.make_github() if self.make_github else GitHubTools(self.settings)
        return self._github


def real_runtime(settings: Settings) -> Runtime:
    return Runtime(
        settings=settings,
        make_llm=lambda: build_tool_llm(settings),
        make_backend=lambda project: MCPClientBackend(settings, project),  # tools through real MCP
        make_approvals=lambda project: IssueApprovalService(settings, channel="web", project_root=project),
        make_embeddings=lambda: get_embedding_provider(settings),
    )


@dataclass
class WebSession:
    id: str
    project: Path
    repo: str | None
    mode: Mode
    investigator: Investigator
    backend: ToolBackend
    approvals: IssueApprovalService
    turns: dict[str, InvestigationResult] = field(default_factory=dict)
    lock: threading.Lock = field(default_factory=threading.Lock)  # one investigation at a time
    created_at: float = field(default_factory=time.time)

    def close(self) -> None:
        self.backend.close()


class SessionManager:
    def __init__(self, runtime: Runtime) -> None:
        self.runtime = runtime
        self._sessions: dict[str, WebSession] = {}
        self._guard = threading.Lock()

    def create(self, project: Path, repo: str | None, mode: Mode) -> WebSession:
        llm = self.runtime.llm
        backend = self.runtime.make_backend(project)
        try:
            investigator = Investigator(self.runtime.settings, llm, backend,
                                        SessionConfig(project_path=project, repo=repo, mode=mode))
        except Exception:
            backend.close()
            raise
        session = WebSession(id=secrets.token_urlsafe(12), project=project, repo=investigator.repo, mode=mode,
                             investigator=investigator, backend=backend,
                             approvals=self.runtime.make_approvals(project))
        with self._guard:
            if len(self._sessions) >= MAX_SESSIONS:  # drop the oldest, closing its MCP server
                oldest = min(self._sessions.values(), key=lambda s: s.created_at)
                self._sessions.pop(oldest.id).close()
            self._sessions[session.id] = session
        return session

    def get(self, session_id: str) -> WebSession | None:
        return self._sessions.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._guard:
            session = self._sessions.pop(session_id, None)
        if session is None:
            return False
        session.close()
        return True

    def find_draft(self, draft_id: str) -> tuple[WebSession, IssueDraft] | None:
        for session in list(self._sessions.values()):
            if draft_id in session.approvals.drafts:
                return session, session.approvals.drafts[draft_id]
        return None

    def close_all(self) -> None:
        for session_id in list(self._sessions):
            self.delete(session_id)
