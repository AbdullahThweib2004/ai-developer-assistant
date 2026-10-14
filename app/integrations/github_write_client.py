"""The only code in the project that writes to GitHub: create one issue.

Deliberately separate from the read client (which stays GET-only):

- It uses GITHUB_WRITE_TOKEN only and checks GITHUB_WRITE_ALLOWED_REPOS itself
  (defense in depth; the approval layer checks too).
- A request hook allows exactly one request shape, ``POST /repos/{owner}/{repo}/issues``
  on the configured API host, and refuses everything else before it is sent.
- A POST is NEVER retried. If a request times out or the connection drops, the
  outcome is UNKNOWN: GitHub may or may not have created the issue. The client
  then looks at the repo's most recent issues (via the read client, which never
  sees the write token) for one with the exact same title and reports what it
  found. Success is reported only for HTTP 201 with an issue number and URL.

This class is called only from the human approval flow (``app.actions.approval``)
and the MCP elicitation-confirmed tool. No LLM tool reaches it.
"""

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from app.config import Settings
from app.integrations.github_client import API_VERSION, ACCEPT, GitHubClient, RepoRef, _user_agent
from app.integrations.github_errors import GitHubError, GitHubNotConfiguredError, RepoNotAllowedError

logger = logging.getLogger(__name__)

RECENT_WINDOW = timedelta(minutes=10)
_ISSUES_PATH = re.compile(r"^/repos/[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9._-]{1,100}/issues$")


class PossibleMatch(BaseModel):
    number: int
    title: str
    html_url: str
    created_at: datetime


class CreateIssueResult(BaseModel):
    """The outcome of one creation attempt. ``created`` only on a confirmed 201."""

    status: Literal["created", "failed", "unknown"]
    repo: str
    number: int | None = None
    html_url: str | None = None
    error_category: str | None = None
    message: str = ""
    possible_matches: list[PossibleMatch] = Field(
        default_factory=list, description="For unknown outcomes: recent issues with exactly the same title."
    )


def write_allowed_repos(settings: Settings) -> list[RepoRef]:
    entries = [e.strip() for e in settings.github_write_allowed_repos.split(",") if e.strip()]
    return [RepoRef.parse(e) for e in entries]


def resolve_write_repo(settings: Settings, repo: str) -> RepoRef:
    """Validate ``repo`` and require it in GITHUB_WRITE_ALLOWED_REPOS (the read allowlist doesn't count)."""
    if not settings.github_write_enabled:
        raise GitHubNotConfiguredError("GitHub write is disabled. Set GITHUB_WRITE_ENABLED=true to allow it.")
    if settings.github_write_token is None:
        raise GitHubNotConfiguredError("GITHUB_WRITE_TOKEN is not set.")
    allowed = write_allowed_repos(settings)
    if not allowed:
        raise GitHubNotConfiguredError("GITHUB_WRITE_ALLOWED_REPOS is empty, so no repository is writable.")
    ref = RepoRef.parse(repo)
    if not any(ref.full_name.lower() == a.full_name.lower() for a in allowed):
        raise RepoNotAllowedError(f"Repository {ref.full_name} is not in GITHUB_WRITE_ALLOWED_REPOS.")
    return ref


def write_configured(settings: Settings) -> bool:
    return bool(settings.github_write_enabled and settings.github_write_token and write_allowed_repos(settings))


class GitHubWriteClient:
    """Creates issues. One operation, one request shape, no retries."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        read_client_factory: Callable[[], GitHubClient] | None = None,
    ) -> None:
        if settings.github_write_token is None:
            raise GitHubNotConfiguredError("GITHUB_WRITE_TOKEN is not set.")
        self.settings = settings
        token = settings.github_write_token.get_secret_value()
        base = urlsplit(settings.github_api_url.rstrip("/"))
        origin = (base.scheme, base.netloc)

        def only_create_issue(request: httpx.Request) -> None:
            if (
                request.method != "POST"
                or (request.url.scheme, request.url.netloc.decode()) != origin
                or not _ISSUES_PATH.match(request.url.path)
            ):
                raise RuntimeError("Refusing request: the write client may only POST /repos/{owner}/{repo}/issues.")

        self._http = httpx.Client(
            base_url=settings.github_api_url.rstrip("/"),
            headers={"Accept": ACCEPT, "X-GitHub-Api-Version": API_VERSION, "User-Agent": _user_agent(),
                     "Authorization": f"Bearer {token}"},
            timeout=settings.github_timeout_seconds,
            follow_redirects=False,
            transport=transport,
            event_hooks={"request": [only_create_issue]},
        )
        # Reads (the unknown-outcome check) go through the read client: read token or none, never the write token.
        self._read_client_factory = read_client_factory or (lambda: GitHubClient(settings))

    def close(self) -> None:
        self._http.close()

    def create_issue(self, repo: str, title: str, body: str, labels: list[str] | None = None) -> CreateIssueResult:
        ref = resolve_write_repo(self.settings, repo)
        payload: dict[str, object] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        started = datetime.now(UTC)
        try:
            response = self._http.post(f"/repos/{ref.full_name}/issues", json=payload)  # never retried
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            logger.warning("Issue creation outcome unknown (%s)", type(exc).__name__)
            return self._unknown(ref, title, started, "timeout" if isinstance(exc, httpx.TimeoutException)
                                 else "connection_error",
                                 f"No response from GitHub ({type(exc).__name__}); the issue may or may not exist.")

        status = response.status_code
        if status == 201:
            try:
                data = response.json()
                number, url = int(data["number"]), str(data["html_url"])
            except (ValueError, KeyError, TypeError):
                return self._unknown(ref, title, started, "invalid_response",
                                     "GitHub returned 201 without an issue number and URL.")
            return CreateIssueResult(status="created", repo=ref.full_name, number=number, html_url=url,
                                     message=f"Created issue #{number}.")
        if status >= 500:
            return self._unknown(ref, title, started, "server_error",
                                 f"GitHub returned HTTP {status}; the issue may or may not have been created.")
        category, message = _error_for(status, response)
        return CreateIssueResult(status="failed", repo=ref.full_name, error_category=category, message=message)

    def _unknown(self, ref: RepoRef, title: str, started: datetime, category: str, message: str) -> CreateIssueResult:
        matches: list[PossibleMatch] = []
        try:
            with self._read_client_factory() as reader:
                recent = reader.list_issues(ref, state="all", limit=30, sort="created")
            matches = [
                PossibleMatch(number=i.number, title=i.title, html_url=i.html_url, created_at=i.created_at)
                for i in recent
                if i.title == title and i.created_at >= started - RECENT_WINDOW
            ]
        except GitHubError as exc:
            message += f" Checking for the issue failed too ({type(exc).__name__}); look at the repository manually."
        if matches:
            message += f" Found {len(matches)} recent issue(s) with exactly this title; it was probably created."
        else:
            message += " No recent issue with this title was found."
        return CreateIssueResult(status="unknown", repo=ref.full_name, error_category=category, message=message,
                                 possible_matches=matches)


def _error_for(status: int, response: httpx.Response) -> tuple[str, str]:
    """Clear error messages without the token, headers or raw responses."""
    if status == 401:
        return "auth", "GitHub rejected the write token (401). Check GITHUB_WRITE_TOKEN."
    if status == 403:
        if response.headers.get("x-ratelimit-remaining") == "0" or "retry-after" in response.headers:
            return "rate_limited", "GitHub rate limit reached (403). Wait and try again later."
        return "forbidden", "GitHub refused (403). The write token needs 'Issues: Read and write' on this repository."
    if status in (404, 410):
        if status == 410:
            return "issues_disabled", "Issues are disabled for this repository (410)."
        return "not_found", "Repository not found (404), or the write token has no access to it."
    if status == 422:
        return "validation", "GitHub rejected the issue (422): invalid fields, or flagged as spam."
    if status == 429:
        return "rate_limited", "GitHub rate limit reached (429). Wait and try again later."
    return "failed", f"GitHub returned HTTP {status}."
