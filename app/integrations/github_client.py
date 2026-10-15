"""A small, read-only GitHub REST client built on httpx.

Design rules:
- GET only. The client has no method that writes, and an httpx request hook
  refuses any non-GET request as a second line of defense.
- Repository identifiers are validated (strict owner/name) before any request,
  and repositories must be on the allowlist (``resolve_repo``).
- Search text from users is sanitized so it can't add qualifiers or escape the
  ``repo:`` scope.
- Pagination follows ``Link: rel="next"`` only on the configured API host, so
  the token is never sent elsewhere, capped by GITHUB_MAX_PAGES / max results.
- Rate limits are reported (RateLimitError with reset time), never waited out.
  Timeouts and 5xx get exactly one retry with backoff.
- The token is only placed in the Authorization header. It never appears in
  logs or error messages.
"""

import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx

from app.config import Settings
from app.integrations.github_errors import (
    AuthError,
    ForbiddenError,
    GitHubError,
    GitHubNotConfiguredError,
    GitHubUnavailableError,
    InvalidRepoError,
    NotFoundError,
    RateLimitError,
    RepoNotAllowedError,
    ValidationError,
)
from app.integrations.github_models import (
    Comment,
    Issue,
    IssueReference,
    IssueSearchResult,
    IssueSummary,
    PullRequest,
    PullRequestFile,
    PullRequestList,
    PullRequestSummary,
    RateLimitStatus,
    Repository,
)

logger = logging.getLogger(__name__)
# httpcore's DEBUG trace logs describe raw request internals; keep them off.
logging.getLogger("httpcore").setLevel(logging.WARNING)

API_VERSION = "2026-03-10"
ACCEPT = "application/vnd.github+json"
RETRY_BACKOFF_SECONDS = 1.0
EXCERPT_CHARS = 300
MAX_PR_FILES = 300
MAX_SEARCH_TEXT = 256  # GitHub rejects longer search text
HEAD_COMMENTS = 2  # get_issue keeps the first comments (the original discussion) ...
COMMENTS_PAGE_SIZE = 100  # ... plus the most recent ones (where resolutions usually are)

State = Literal["open", "closed", "all"]
Kind = Literal["issue", "pr", "any"]

_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_NAME = re.compile(r"^[A-Za-z0-9._-]{1,100}$")


# ---- repository identifiers and allowlist ----------------------------------------


@dataclass(frozen=True)
class RepoRef:
    owner: str
    name: str

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @classmethod
    def parse(cls, value: str) -> "RepoRef":
        """Strictly parse "owner/name"; raise InvalidRepoError otherwise."""
        parts = value.strip().split("/") if isinstance(value, str) else []
        if len(parts) != 2:
            raise InvalidRepoError("Repository must be in the form owner/name.")
        owner, name = parts
        if not _OWNER.match(owner) or not _NAME.match(name) or name in (".", "..") or name.endswith(".git"):
            raise InvalidRepoError("Repository must be in the form owner/name (letters, digits, '-', '_', '.').")
        return cls(owner, name)


def allowed_repos(settings: Settings) -> list[RepoRef]:
    """GITHUB_ALLOWED_REPOS, or just GITHUB_DEFAULT_REPO when the list is empty."""
    entries = [e.strip() for e in settings.github_allowed_repos.split(",") if e.strip()]
    if not entries and settings.github_default_repo:
        entries = [settings.github_default_repo]
    if not entries:
        raise GitHubNotConfiguredError(
            "GitHub is not configured. Set GITHUB_DEFAULT_REPO (owner/name) or GITHUB_ALLOWED_REPOS."
        )
    return [RepoRef.parse(e) for e in entries]


def resolve_repo(settings: Settings, repo: str | None) -> RepoRef:
    """The repo to use (argument or GITHUB_DEFAULT_REPO), checked against the allowlist."""
    allowed = allowed_repos(settings)
    if not repo:
        if not settings.github_default_repo:
            raise GitHubNotConfiguredError("No repository given and GITHUB_DEFAULT_REPO is not set.")
        repo = settings.github_default_repo
    ref = RepoRef.parse(repo)
    if not any(ref.full_name.lower() == a.full_name.lower() for a in allowed):
        raise RepoNotAllowedError(f"Repository {ref.full_name} is not in GITHUB_ALLOWED_REPOS.")
    return ref


# ---- pure helpers ----------------------------------------------------------------

_OPERATORS = {"AND", "OR", "NOT"}
# Qualifiers from GitHub's "Searching issues and pull requests" docs, plus a few
# general search keywords (sort, has, parent-issue, sub-issue). Tokens using one
# of these are dropped; any other "word:value" keeps its words.
KNOWN_QUALIFIERS = frozenset({
    "type", "is", "in", "user", "org", "repo", "state", "archived", "reason", "author", "assignee",
    "mentions", "team", "commenter", "involves", "linked", "label", "milestone", "project", "status",
    "head", "base", "language", "comments", "interactions", "reactions", "draft", "review",
    "reviewed-by", "review-requested", "review-involves", "user-review-requested",
    "team-review-requested", "team-review-requested-user", "created", "updated", "closed", "merged",
    "no", "sort", "has", "parent-issue", "sub-issue",
})


def _is_qualifier(token: str) -> bool:
    """True for "qualifier:value", also when negated, quoted or parenthesized."""
    key, sep, _ = token.lstrip("-(\"'").partition(":")
    return bool(sep) and key.lower() in KNOWN_QUALIFIERS


def sanitize_search_text(text: str) -> str:
    """Reduce user text to plain search terms.

    Drops anything that could change the query's scope: known GitHub
    qualifiers (``repo:x/y``, ``org:``, ``is:``, ``label:``...), boolean
    operators, negation, quotes and parentheses. Other colons are treated as
    spaces, so error text such as ``KeyError: 'token'`` or
    ``jwt.exceptions:DecodeError`` keeps its words. Capped at 256 characters.
    """
    terms = []
    for raw in text.split():
        if _is_qualifier(raw):
            continue
        for piece in raw.replace(":", " ").split():
            term = re.sub(r"[^\w.#+/@'-]", "", piece).lstrip("-").strip("'")
            if term and term not in _OPERATORS:
                terms.append(term)
    return " ".join(terms)[:MAX_SEARCH_TEXT].strip()


def build_search_query(repo: RepoRef, text: str, state: State = "all", kind: Kind = "any") -> str:
    """A /search/issues query that is always scoped to exactly one repo."""
    terms = sanitize_search_text(text)
    if not terms:
        raise ValidationError("The search text is empty after removing qualifiers and operators.")
    qualifiers = [f"repo:{repo.full_name}"]
    if kind == "issue":
        qualifiers.append("is:issue")
    elif kind == "pr":
        qualifiers.append("is:pr")
    if state in ("open", "closed"):
        qualifiers.append(f"state:{state}")
    return " ".join([terms, *qualifiers])


_CLOSING = re.compile(
    r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s+"
    r"(?:https://github\.com/([A-Za-z0-9-]+/[A-Za-z0-9._-]+)/issues/(\d+)"
    r"|([A-Za-z0-9-]+/[A-Za-z0-9._-]+)?#(\d+))",
    re.IGNORECASE,
)


def parse_closing_references(body: str, repo: RepoRef) -> list[IssueReference]:
    """Issues referenced with GitHub closing keywords ("fixes #12", "closes o/r#3", URLs)."""
    refs: list[IssueReference] = []
    seen: set[tuple[str, int]] = set()
    for match in _CLOSING.finditer(body or ""):
        keyword, url_repo, url_number, short_repo, short_number = match.groups()
        target = (url_repo or short_repo or repo.full_name)
        number = int(url_number or short_number)
        key = (target.lower(), number)
        if key not in seen:
            seen.add(key)
            refs.append(IssueReference(keyword=keyword.lower(), repo=target, number=number))
    return refs


def _comment_gap(comments: list[Comment], total: int) -> tuple[int, int]:
    """(number of omitted comments, position after which they were omitted)."""
    omitted = max(total - len(comments), 0)
    for before, after in zip(comments, comments[1:], strict=False):
        if after.position - before.position > 1:
            return omitted, before.position
    return omitted, comments[-1].position if comments else 0


def truncate(text: str | None, limit: int) -> tuple[str, bool]:
    text = text or ""
    return (text, False) if len(text) <= limit else (text[:limit].rstrip() + "…", True)


def excerpt(text: str | None, limit: int = EXCERPT_CHARS) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def redact_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    """Headers safe to print in debug output (Authorization and cookies redacted)."""
    sensitive = {"authorization", "cookie", "set-cookie", "proxy-authorization"}
    return {k: ("[REDACTED]" if k.lower() in sensitive else v) for k, v in dict(headers).items()}


def parse_rate_limit(headers: httpx.Headers, authenticated: bool) -> RateLimitStatus:
    def as_int(name: str) -> int | None:
        value = headers.get(name)
        return int(value) if value and value.isdigit() else None

    reset = as_int("x-ratelimit-reset")
    return RateLimitStatus(
        limit=as_int("x-ratelimit-limit"),
        remaining=as_int("x-ratelimit-remaining"),
        used=as_int("x-ratelimit-used"),
        reset_at=datetime.fromtimestamp(reset, tz=UTC) if reset is not None else None,
        resource=headers.get("x-ratelimit-resource"),
        authenticated=authenticated,
    )


def _next_link(link_header: str | None) -> str | None:
    for part in (link_header or "").split(","):
        match = re.match(r'\s*<([^>]+)>\s*;\s*rel="([^"]+)"', part)
        if match and match.group(2) == "next":
            return match.group(1)
    return None


def _user_agent() -> str:
    try:
        return f"ai-developer-assistant/{version('ai-developer-assistant')}"
    except PackageNotFoundError:
        return "ai-developer-assistant"


def _only_get(request: httpx.Request) -> None:
    """Event hook: the client must never send anything but GET."""
    if request.method != "GET":
        raise RuntimeError(f"Refusing to send a {request.method} request: the GitHub client is read-only.")


# ---- client ------------------------------------------------------------------------


class GitHubClient:
    """Read-only access to repositories, issues and pull requests."""

    def __init__(
        self,
        settings: Settings,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self._token = settings.github_token.get_secret_value() if settings.github_token else None
        base = urlsplit(settings.github_api_url.rstrip("/"))
        self._origin = (base.scheme, base.netloc)
        headers = {"Accept": ACCEPT, "X-GitHub-Api-Version": API_VERSION, "User-Agent": _user_agent()}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        self._http = httpx.Client(
            base_url=settings.github_api_url.rstrip("/"),
            headers=headers,
            timeout=settings.github_timeout_seconds,
            follow_redirects=False,  # never forward the token to another location
            transport=transport,
            event_hooks={"request": [_only_get]},
        )
        self.last_rate_limit: RateLimitStatus | None = None

    @property
    def authenticated(self) -> bool:
        return self._token is not None

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "GitHubClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ---- operations ----------------------------------------------------------------

    def get_repository(self, repo: RepoRef) -> Repository:
        data = self._get(f"/repos/{repo.full_name}").json()
        license_info = data.get("license") or {}
        return Repository(
            full_name=data["full_name"],
            description=data.get("description"),
            default_branch=data.get("default_branch", ""),
            stars=data.get("stargazers_count", 0),
            forks=data.get("forks_count", 0),
            open_issues_count=data.get("open_issues_count", 0),
            language=data.get("language"),
            topics=data.get("topics") or [],
            license=license_info.get("spdx_id") or license_info.get("name"),
            html_url=data["html_url"],
            visibility=data.get("visibility"),
            pushed_at=data.get("pushed_at"),
            rate_limit=self.last_rate_limit,
        )

    def search_issues(
        self, repo: RepoRef, query: str, state: State = "all", kind: Kind = "any", limit: int | None = None
    ) -> IssueSearchResult:
        limit = self._limit(limit)
        q = build_search_query(repo, query, state, kind)
        items, truncated, first = self._get_paginated("/search/issues", {"q": q}, limit, items_key="items")
        expected = f"/repos/{repo.full_name}".lower()
        # Defense in depth: drop anything GitHub returns from another repository.
        in_repo = [i for i in items if str(i.get("repository_url", "")).lower().endswith(expected)]
        if len(in_repo) != len(items):
            logger.warning("Dropped %d search results outside %s", len(items) - len(in_repo), repo.full_name)
        total = int(first.get("total_count", len(in_repo)))
        return IssueSearchResult(
            repo=repo.full_name,
            query=q,
            total_count=total,
            incomplete_results=bool(first.get("incomplete_results", False)),
            items=[self._issue_summary(i) for i in in_repo],
            truncated=truncated or total > len(in_repo),
            rate_limit=self.last_rate_limit,
        )

    def get_issue(self, repo: RepoRef, number: int, include_comments: bool = True) -> Issue:
        self._check_number(number)
        data = self._get(f"/repos/{repo.full_name}/issues/{number}").json()
        is_pr = "pull_request" in data
        body, body_truncated = truncate(data.get("body"), self.settings.github_max_body_chars)
        comments_count = int(data.get("comments", 0))
        comments: list[Comment] = []
        if include_comments and comments_count and self.settings.github_max_comments:
            comments = self._issue_thread(repo, number, comments_count)
        omitted, omitted_after = _comment_gap(comments, comments_count) if include_comments else (comments_count, 0)
        note = None
        if is_pr:
            note = (f"#{number} is a pull request. Use get_pull_request for its branches, "
                    "merge state and changed files.")
        return Issue(
            repo=repo.full_name, number=data["number"], title=data.get("title", ""), state=data.get("state", ""),
            state_reason=data.get("state_reason"), is_pull_request=is_pr,
            labels=[label["name"] for label in data.get("labels", []) if isinstance(label, dict)],
            author=(data.get("user") or {}).get("login"), created_at=data["created_at"],
            updated_at=data["updated_at"], closed_at=data.get("closed_at"), html_url=data["html_url"],
            body=body, body_truncated=body_truncated, comments_count=comments_count, comments=comments,
            comments_truncated=omitted > 0, omitted_comments=omitted,
            omitted_after_position=omitted_after if omitted else None,
            note=note, rate_limit=self.last_rate_limit,
        )

    def _issue_thread(self, repo: RepoRef, number: int, total: int) -> list[Comment]:
        """The first HEAD_COMMENTS comments plus the most recent ones, in chronological order.

        Comments are ordered by ascending ID, so positions map to pages of 100.
        Only the pages holding the wanted positions are fetched (one request
        for threads of up to 100 comments).
        """
        limit = self.settings.github_max_comments
        if total <= limit:
            wanted = list(range(total))
        else:
            head = min(HEAD_COMMENTS, limit)
            wanted = list(range(head)) + list(range(total - (limit - head), total))
        by_position: dict[int, dict[str, Any]] = {}
        for page in sorted({i // COMMENTS_PAGE_SIZE for i in wanted}):
            raw = self._get(
                f"/repos/{repo.full_name}/issues/{number}/comments",
                {"per_page": COMMENTS_PAGE_SIZE, "page": page + 1},
            ).json()
            for offset, item in enumerate(raw):
                by_position[page * COMMENTS_PAGE_SIZE + offset] = item
        comments = []
        for position in wanted:
            c = by_position.get(position)
            if c is None:  # the thread changed since the issue was fetched
                continue
            text, cut = truncate(c.get("body"), self.settings.github_max_body_chars)
            comments.append(Comment(
                position=position + 1, author=(c.get("user") or {}).get("login"),
                author_association=c.get("author_association"), created_at=c["created_at"],
                body=text, body_truncated=cut, html_url=c["html_url"],
            ))
        return comments

    def list_issues(
        self, repo: RepoRef, state: State = "open", limit: int = 100, sort: Literal["created", "updated"] = "created"
    ) -> list[IssueSummary]:
        """Recent issues (not PRs), newest first. Unlike search, this has no indexing delay."""
        limit = max(1, min(limit, 100))
        items, _, _ = self._get_paginated(
            f"/repos/{repo.full_name}/issues", {"state": state, "sort": sort, "direction": "desc"}, limit
        )
        return [self._issue_summary(i) for i in items if "pull_request" not in i]

    def list_pull_requests(self, repo: RepoRef, state: State = "open", limit: int | None = None) -> PullRequestList:
        limit = self._limit(limit)
        items, truncated, _ = self._get_paginated(f"/repos/{repo.full_name}/pulls", {"state": state}, limit)
        return PullRequestList(
            repo=repo.full_name, state=state, truncated=truncated, rate_limit=self.last_rate_limit,
            items=[
                PullRequestSummary(
                    number=p["number"], title=p.get("title", ""), state=p.get("state", ""),
                    draft=bool(p.get("draft")), author=(p.get("user") or {}).get("login"),
                    base_branch=(p.get("base") or {}).get("ref", ""), head_branch=(p.get("head") or {}).get("ref", ""),
                    created_at=p["created_at"], updated_at=p["updated_at"], merged_at=p.get("merged_at"),
                    html_url=p["html_url"],
                )
                for p in items
            ],
        )

    def get_pull_request(self, repo: RepoRef, number: int) -> PullRequest:
        self._check_number(number)
        data = self._get(f"/repos/{repo.full_name}/pulls/{number}").json()
        raw_files, files_truncated, _ = self._get_paginated(
            f"/repos/{repo.full_name}/pulls/{number}/files", {}, MAX_PR_FILES
        )
        body, body_truncated = truncate(data.get("body"), self.settings.github_max_body_chars)
        changed = data.get("changed_files")
        return PullRequest(
            repo=repo.full_name, number=data["number"], title=data.get("title", ""), state=data.get("state", ""),
            draft=bool(data.get("draft")), merged=bool(data.get("merged") or data.get("merged_at")),
            merged_at=data.get("merged_at"), author=(data.get("user") or {}).get("login"),
            base_branch=(data.get("base") or {}).get("ref", ""), head_branch=(data.get("head") or {}).get("ref", ""),
            created_at=data["created_at"], updated_at=data["updated_at"], closed_at=data.get("closed_at"),
            html_url=data["html_url"], body=body, body_truncated=body_truncated,
            commits=data.get("commits"), additions=data.get("additions"), deletions=data.get("deletions"),
            changed_files=changed,
            files=[
                PullRequestFile(filename=f["filename"], status=f.get("status", ""), additions=f.get("additions", 0),
                                deletions=f.get("deletions", 0), previous_filename=f.get("previous_filename"))
                for f in raw_files
            ],
            files_truncated=files_truncated or (changed is not None and changed > len(raw_files)),
            closing_references=parse_closing_references(data.get("body") or "", repo),
            rate_limit=self.last_rate_limit,
        )

    # ---- HTTP plumbing (GET only) -------------------------------------------------

    def _limit(self, limit: int | None) -> int:
        cap = self.settings.github_max_results
        return cap if limit is None else max(1, min(limit, cap))

    @staticmethod
    def _check_number(number: int) -> None:
        if not isinstance(number, int) or number < 1:
            raise ValidationError("Issue/pull request numbers are positive integers.")

    def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        """GET with one retry (with backoff) for timeouts and 5xx responses."""
        for attempt in range(2):
            try:
                response = self._http.get(url, params=params)
            except httpx.TimeoutException:
                if attempt == 0:
                    self._sleep(RETRY_BACKOFF_SECONDS)
                    continue
                raise GitHubUnavailableError("GitHub did not respond in time (after one retry).") from None
            except httpx.TransportError as exc:
                raise GitHubUnavailableError(f"Could not reach GitHub ({type(exc).__name__}).") from None
            self.last_rate_limit = parse_rate_limit(response.headers, self.authenticated)
            logger.debug("GET %s -> %s (rate remaining=%s)", response.request.url.path, response.status_code,
                         self.last_rate_limit.remaining)
            if response.status_code >= 500 and attempt == 0:
                self._sleep(RETRY_BACKOFF_SECONDS)
                continue
            self._raise_for_status(response)
            return response
        raise AssertionError("unreachable")

    def _get_paginated(
        self, path: str, params: dict[str, Any], limit: int, items_key: str | None = None
    ) -> tuple[list[dict[str, Any]], bool, dict[str, Any]]:
        """Follow Link rel="next" until ``limit`` items or GITHUB_MAX_PAGES pages.

        Returns (items, truncated, first_page_json_object_or_empty).
        """
        items: list[dict[str, Any]] = []
        first: dict[str, Any] = {}
        url: str | None = path
        request_params: dict[str, Any] | None = {**params, "per_page": min(100, limit)}
        pages = 0
        while url and pages < self.settings.github_max_pages and len(items) < limit:
            response = self._get(url, request_params)
            payload = response.json()
            if pages == 0 and isinstance(payload, dict):
                first = payload
            page_items = payload.get(items_key, []) if items_key else payload
            items.extend(page_items)
            pages += 1
            url = self._safe_next(response.headers.get("link"))
            request_params = None  # the next URL already carries every parameter
        truncated = len(items) > limit or url is not None
        return items[:limit], truncated, first

    def _safe_next(self, link_header: str | None) -> str | None:
        """The next-page URL, but only if it points at the configured API host."""
        next_url = _next_link(link_header)
        if next_url is None:
            return None
        parts = urlsplit(next_url)
        if (parts.scheme, parts.netloc) != self._origin:
            logger.warning("Ignoring pagination link to a different host")
            return None
        return next_url

    def _raise_for_status(self, response: httpx.Response) -> None:
        status = response.status_code
        if status < 300:
            return
        message = self._github_message(response)
        if 300 <= status < 400:
            raise NotFoundError(f"The resource moved (HTTP {status}); it may have been renamed or transferred.")
        if status == 401:
            raise AuthError("GitHub rejected the token (401). Check that GITHUB_TOKEN is valid and not expired.")
        if status in (403, 429) and self._is_rate_limited(response, message):
            raise self._rate_limit_error(response)
        if status == 403:
            raise ForbiddenError("GitHub refused access (403). The token may lack read permission for this repository.")
        if status in (404, 410):
            raise NotFoundError("Not found on GitHub (404). It doesn't exist, "
                                "or it's private and the token can't see it.")
        if status == 422:
            raise ValidationError(f"GitHub rejected the request (422): {message}")
        if status >= 500:
            raise GitHubUnavailableError(f"GitHub is unavailable (HTTP {status}) after one retry.")
        raise GitHubError(f"Unexpected GitHub response (HTTP {status}).")

    def _is_rate_limited(self, response: httpx.Response, message: str) -> bool:
        headers = response.headers
        return (
            response.status_code == 429
            or headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in headers
            or "rate limit" in message.lower()
        )

    def _rate_limit_error(self, response: httpx.Response) -> RateLimitError:
        retry_after = response.headers.get("retry-after")
        retry_seconds = int(retry_after) if retry_after and retry_after.isdigit() else None
        reset_at = self.last_rate_limit.reset_at if self.last_rate_limit else None
        when = []
        if retry_seconds is not None:
            when.append(f"retry after {retry_seconds}s")
        if reset_at is not None:
            when.append(f"limit resets at {reset_at.isoformat()}")
        hint = "" if self.authenticated else " Setting GITHUB_TOKEN raises the limit (60/h unauthenticated)."
        detail = f" ({'; '.join(when)})" if when else ""
        return RateLimitError(f"GitHub rate limit reached{detail}.{hint}", reset_at, retry_seconds)

    def _github_message(self, response: httpx.Response) -> str:
        """GitHub's own error text (from the JSON body), scrubbed and shortened."""
        try:
            body = response.json()
        except ValueError:
            return ""
        if not isinstance(body, dict):
            return ""
        parts = [str(body.get("message", ""))]
        for err in body.get("errors", []) or []:
            if isinstance(err, dict) and err.get("message"):
                parts.append(str(err["message"]))
        text = "; ".join(p for p in parts if p)
        if self._token:
            text = text.replace(self._token, "[REDACTED]")
        return text[:300]

    @staticmethod
    def _issue_summary(item: dict[str, Any]) -> IssueSummary:
        return IssueSummary(
            number=item["number"], title=item.get("title", ""), state=item.get("state", ""),
            is_pull_request="pull_request" in item,
            labels=[label["name"] for label in item.get("labels", []) if isinstance(label, dict)],
            author=(item.get("user") or {}).get("login"), created_at=item["created_at"],
            updated_at=item["updated_at"], comments=int(item.get("comments", 0)), html_url=item["html_url"],
            body_excerpt=excerpt(item.get("body")),
        )
