"""Read-only GitHub tools: allowlist checks + the shared GitHubClient + untrusted wrapping.

The CLI uses the same ``GitHubClient`` and ``resolve_repo``; this module only adds
what MCP clients need: every piece of user-written GitHub text (titles,
descriptions, bodies, comments) is wrapped in <untrusted-github-content> tags.
"""

import httpx

from app.config import Settings
from app.integrations.github_client import GitHubClient, Kind, State, resolve_repo
from app.integrations.github_models import Issue, IssueSearchResult, PullRequest, PullRequestList, Repository
from app.mcp_server.security import GITHUB_UNTRUSTED_TAG, wrap_untrusted


def _wrap(text: str | None, source: str) -> str:
    return wrap_untrusted(text or "", source, tag=GITHUB_UNTRUSTED_TAG)


class GitHubTools:
    """Creates one client lazily, so the server starts even when GitHub isn't configured."""

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None) -> None:
        self.settings = settings
        self._transport = transport
        self._client: GitHubClient | None = None

    @property
    def client(self) -> GitHubClient:
        if self._client is None:
            self._client = GitHubClient(self.settings, transport=self._transport)
        return self._client

    def get_repository(self, repo: str | None) -> Repository:
        ref = resolve_repo(self.settings, repo)
        result = self.client.get_repository(ref)
        if result.description:
            result.description = _wrap(result.description, f"{ref.full_name} description")
        return result

    def search_issues(
        self, repo: str | None, query: str, state: State, kind: Kind, limit: int | None
    ) -> IssueSearchResult:
        ref = resolve_repo(self.settings, repo)
        result = self.client.search_issues(ref, query, state=state, kind=kind, limit=limit)
        for item in result.items:
            source = f"{ref.full_name}#{item.number}"
            item.title = _wrap(item.title, f"{source} title")
            item.body_excerpt = _wrap(item.body_excerpt, f"{source} body")
        return result

    def get_issue(self, repo: str | None, number: int, include_comments: bool) -> Issue:
        ref = resolve_repo(self.settings, repo)
        issue = self.client.get_issue(ref, number, include_comments=include_comments)
        source = f"{ref.full_name}#{number}"
        issue.title = _wrap(issue.title, f"{source} title")
        issue.body = _wrap(issue.body, f"{source} body")
        for comment in issue.comments:
            comment.body = _wrap(comment.body, f"{source} comment by {comment.author or 'unknown'}")
        return issue

    def list_pull_requests(self, repo: str | None, state: State, limit: int | None) -> PullRequestList:
        ref = resolve_repo(self.settings, repo)
        result = self.client.list_pull_requests(ref, state=state, limit=limit)
        for pr in result.items:
            pr.title = _wrap(pr.title, f"{ref.full_name}#{pr.number} title")
        return result

    def get_pull_request(self, repo: str | None, number: int) -> PullRequest:
        ref = resolve_repo(self.settings, repo)
        pr = self.client.get_pull_request(ref, number)
        source = f"{ref.full_name}#{number}"
        pr.title = _wrap(pr.title, f"{source} title")
        pr.body = _wrap(pr.body, f"{source} body")
        return pr
