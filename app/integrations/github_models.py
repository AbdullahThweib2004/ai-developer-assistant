"""Normalized, read-only views of GitHub data.

Only the fields the assistant needs are kept, with stable names and types, so
callers never depend on GitHub's raw JSON. Long text is capped and flagged.
"""

from datetime import datetime

from pydantic import BaseModel, Field


class RateLimitStatus(BaseModel):
    """From the x-ratelimit-* headers of the last response."""

    limit: int | None = None
    remaining: int | None = None
    used: int | None = None
    reset_at: datetime | None = None
    resource: str | None = None  # e.g. "core" or "search" (separate budgets)
    authenticated: bool = False


class Repository(BaseModel):
    full_name: str
    description: str | None = None
    default_branch: str
    stars: int
    forks: int
    open_issues_count: int = Field(description="Open issues plus open pull requests (GitHub counts both).")
    language: str | None = None
    topics: list[str] = Field(default_factory=list)
    license: str | None = None
    html_url: str
    visibility: str | None = None
    pushed_at: datetime | None = None
    rate_limit: RateLimitStatus | None = None


class IssueSummary(BaseModel):
    """One search result (issue or pull request)."""

    number: int
    title: str
    state: str
    is_pull_request: bool
    labels: list[str] = Field(default_factory=list)
    author: str | None = None
    created_at: datetime
    updated_at: datetime
    comments: int
    html_url: str
    body_excerpt: str = ""


class IssueSearchResult(BaseModel):
    repo: str
    query: str = Field(description="The exact query sent to GitHub, including the repo: qualifier.")
    total_count: int
    incomplete_results: bool
    items: list[IssueSummary]
    truncated: bool = Field(description="True if more results exist than were returned.")
    rate_limit: RateLimitStatus | None = None


class Comment(BaseModel):
    position: int = Field(description="1-based position of the comment in the thread.")
    author: str | None = None
    author_association: str | None = None
    created_at: datetime
    body: str
    body_truncated: bool = False
    html_url: str


class Issue(BaseModel):
    repo: str
    number: int
    title: str
    state: str
    state_reason: str | None = None
    is_pull_request: bool
    labels: list[str] = Field(default_factory=list)
    author: str | None = None
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    html_url: str
    body: str = ""
    body_truncated: bool = False
    comments_count: int
    comments: list[Comment] = Field(
        default_factory=list,
        description="Chronological: the first 2 comments, then the most recent ones (see omitted_comments).",
    )
    comments_truncated: bool = Field(default=False, description="True if some comments were omitted.")
    omitted_comments: int = Field(default=0, description="Comments left out between the first and the latest ones.")
    omitted_after_position: int | None = Field(
        default=None, description="The omitted comments come right after the comment at this position."
    )
    note: str | None = None
    rate_limit: RateLimitStatus | None = None


class PullRequestSummary(BaseModel):
    number: int
    title: str
    state: str
    draft: bool = False
    author: str | None = None
    base_branch: str
    head_branch: str
    created_at: datetime
    updated_at: datetime
    merged_at: datetime | None = None
    html_url: str


class PullRequestList(BaseModel):
    repo: str
    state: str
    items: list[PullRequestSummary]
    truncated: bool
    rate_limit: RateLimitStatus | None = None


class PullRequestFile(BaseModel):
    filename: str
    status: str  # added, removed, modified, renamed, copied, changed, unchanged
    additions: int
    deletions: int
    previous_filename: str | None = None


class IssueReference(BaseModel):
    """An issue a PR claims to close via a closing keyword (e.g. "fixes #12")."""

    keyword: str
    repo: str = Field(description="owner/name; the PR's own repo when the reference was just #N.")
    number: int


class PullRequest(BaseModel):
    repo: str
    number: int
    title: str
    state: str
    draft: bool = False
    merged: bool
    merged_at: datetime | None = None
    author: str | None = None
    base_branch: str
    head_branch: str
    created_at: datetime
    updated_at: datetime
    closed_at: datetime | None = None
    html_url: str
    body: str = ""
    body_truncated: bool = False
    commits: int | None = None
    additions: int | None = None
    deletions: int | None = None
    changed_files: int | None = None
    files: list[PullRequestFile] = Field(default_factory=list)
    files_truncated: bool = False
    closing_references: list[IssueReference] = Field(default_factory=list)
    rate_limit: RateLimitStatus | None = None
