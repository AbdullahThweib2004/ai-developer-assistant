"""Tests for the read-only GitHub client (httpx.MockTransport, no network)."""

import inspect
import logging
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.integrations.github_client import (
    API_VERSION,
    GitHubClient,
    RepoRef,
    build_search_query,
    parse_closing_references,
    redact_headers,
    resolve_repo,
    sanitize_search_text,
)
from app.integrations.github_errors import (
    AuthError,
    ForbiddenError,
    GitHubNotConfiguredError,
    GitHubUnavailableError,
    InvalidRepoError,
    NotFoundError,
    RateLimitError,
    RepoNotAllowedError,
    ValidationError,
)
from tests.helpers import GitHubMock, github_fixture, make_settings

TOKEN = "github_pat_TEST_DO_NOT_LEAK_0123456789"
REPO = RepoRef("octo-org", "demo")
BASE = "/repos/octo-org/demo"


def settings(tmp_path: Path, **overrides: object) -> Settings:
    defaults: dict[str, object] = {"github_default_repo": "octo-org/demo"}
    return make_settings(tmp_path, **{**defaults, **overrides})


def client(tmp_path: Path, routes: dict, sleeps: list[float] | None = None, **overrides: object):
    mock = GitHubMock(routes)
    sleep_log = sleeps if sleeps is not None else []
    gh = GitHubClient(settings(tmp_path, **overrides), transport=mock.transport, sleep=sleep_log.append)
    return gh, mock


# ---- headers and secrets ------------------------------------------------------------


def test_headers_without_token(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {BASE: (200, github_fixture("repo.json"), {})})
    gh.get_repository(REPO)
    headers = mock.requests[0].headers
    assert "authorization" not in headers
    assert headers["x-github-api-version"] == API_VERSION
    assert headers["accept"] == "application/vnd.github+json"
    assert headers["user-agent"].startswith("ai-developer-assistant")
    assert not gh.authenticated


def test_auth_header_only_when_token_set(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {BASE: (200, github_fixture("repo.json"), {})}, github_token=TOKEN)
    repo = gh.get_repository(REPO)
    assert mock.requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert repo.rate_limit.authenticated is True


def test_token_never_in_logs_or_errors(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    echo = {"message": f"Bad credentials for {TOKEN}"}  # even if GitHub echoed it back
    routes = {
        BASE: (401, echo, {}),
        f"{BASE}/issues/1": (422, {"message": f"invalid {TOKEN}"}, {}),
        f"{BASE}/issues/2": (403, {"message": "Resource not accessible"}, {}),
    }
    gh, _ = client(tmp_path, routes, github_token=TOKEN)
    messages = []
    with caplog.at_level(logging.DEBUG):
        for call in (lambda: gh.get_repository(REPO), lambda: gh.get_issue(REPO, 1), lambda: gh.get_issue(REPO, 2)):
            with pytest.raises(Exception) as excinfo:
                call()
            messages.append(str(excinfo.value))
    assert all(TOKEN not in m for m in messages)
    assert TOKEN not in caplog.text and "Bearer" not in caplog.text
    assert TOKEN not in repr(gh.settings)


def test_redact_headers() -> None:
    redacted = redact_headers({"Authorization": f"Bearer {TOKEN}", "Accept": "x"})
    assert redacted == {"Authorization": "[REDACTED]", "Accept": "x"}


# ---- read-only guarantee -------------------------------------------------------------


def test_client_has_no_write_methods() -> None:
    public = [name for name, _ in inspect.getmembers(GitHubClient, inspect.isfunction) if not name.startswith("_")]
    assert set(public) == {"get_repository", "search_issues", "get_issue", "list_pull_requests",
                           "get_pull_request", "close"}
    write_words = ("post", "patch", "put", "delete", "create", "update", "edit", "comment", "label", "merge")
    for name, _ in inspect.getmembers(GitHubClient, inspect.isfunction):
        assert not any(word in name.lower() for word in write_words), name


def test_non_get_requests_are_refused_before_sending(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {})
    for method in ("POST", "PATCH", "PUT", "DELETE"):
        with pytest.raises(RuntimeError, match="read-only"):
            gh._http.request(method, f"{BASE}/issues")
    assert mock.requests == []


def test_every_request_is_a_get(tmp_path: Path) -> None:
    routes = {
        BASE: (200, github_fixture("repo.json"), {}),
        "/search/issues": (200, github_fixture("search_issues.json"), {}),
        f"{BASE}/issues/12": (200, github_fixture("issue.json"), {}),
        f"{BASE}/issues/12/comments": (200, github_fixture("issue_comments.json"), {}),
        f"{BASE}/pulls": (200, github_fixture("pulls.json"), {}),
        f"{BASE}/pulls/15": (200, github_fixture("pull.json"), {}),
        f"{BASE}/pulls/15/files": (200, github_fixture("pull_files.json"), {}),
    }
    gh, mock = client(tmp_path, routes)
    gh.get_repository(REPO)
    gh.search_issues(REPO, "token")
    gh.get_issue(REPO, 12)
    gh.list_pull_requests(REPO)
    gh.get_pull_request(REPO, 15)
    assert {r.method for r in mock.requests} == {"GET"}


# ---- repo validation and allowlist ----------------------------------------------------


@pytest.mark.parametrize("bad", ["", "demo", "a/b/c", "../x", "a/..", "-a/b", "a b/c", "a/b?x=1", "a/b#1",
                                 "owner/name.git", "a/", "/b", "x" * 40 + "/b", "a/" + "b" * 101])
def test_invalid_repo_identifiers(bad: str) -> None:
    with pytest.raises(InvalidRepoError):
        RepoRef.parse(bad)


def test_valid_repo_identifiers() -> None:
    assert RepoRef.parse("fastapi/fastapi").full_name == "fastapi/fastapi"
    assert RepoRef.parse(" my-org/repo.name_x ").full_name == "my-org/repo.name_x"


def test_allowlist(tmp_path: Path) -> None:
    only_default = settings(tmp_path)
    assert resolve_repo(only_default, None) == REPO
    assert resolve_repo(only_default, "OCTO-ORG/Demo").full_name == "OCTO-ORG/Demo"  # case-insensitive match
    with pytest.raises(RepoNotAllowedError):
        resolve_repo(only_default, "fastapi/fastapi")

    listed = settings(tmp_path, github_allowed_repos="fastapi/fastapi, octo-org/demo")
    assert resolve_repo(listed, "fastapi/fastapi").full_name == "fastapi/fastapi"
    with pytest.raises(RepoNotAllowedError):
        resolve_repo(listed, "evil/repo")

    no_default = settings(tmp_path, github_default_repo=None, github_allowed_repos="fastapi/fastapi")
    with pytest.raises(GitHubNotConfiguredError, match="GITHUB_DEFAULT_REPO"):
        resolve_repo(no_default, None)

    nothing = settings(tmp_path, github_default_repo=None)
    with pytest.raises(GitHubNotConfiguredError, match="not configured"):
        resolve_repo(nothing, "fastapi/fastapi")


def test_bad_repo_rejected_before_any_request(tmp_path: Path) -> None:
    with pytest.raises(InvalidRepoError):
        resolve_repo(settings(tmp_path), "../../etc")


# ---- search query safety ----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "401 error repo:other/x",
        "token repo:other/x is:pr org:evil user:mallory",
        "token OR repo:other/x",
        "token -repo:octo-org/demo",
        'token "repo:other/x"',
        "token (repo:other/x OR is:issue) NOT label:bug",
        "token\trepo:other/x\nin:title",
    ],
)
def test_search_query_cannot_escape_repo(text: str) -> None:
    query = build_search_query(REPO, text, state="all", kind="any")
    assert query.count("repo:") == 1 and query.endswith("repo:octo-org/demo")
    for forbidden in ("other/x", "org:", "user:", "is:", "label:", "in:", " OR ", " NOT ", "(", '"', "-repo"):
        assert forbidden not in query
    assert query.startswith("token") or query.startswith("401")


def test_search_query_qualifiers_from_parameters() -> None:
    assert build_search_query(REPO, "crash", "open", "issue") == "crash repo:octo-org/demo is:issue state:open"
    assert build_search_query(REPO, "crash", "closed", "pr") == "crash repo:octo-org/demo is:pr state:closed"


def test_search_text_sanitizing_keeps_useful_terms() -> None:
    assert sanitize_search_text("Error: 401 Unauthorized bearer-token #12 C++") == "Error 401 Unauthorized bearer-token #12 C++"
    assert len(sanitize_search_text("word " * 200)) <= 256
    with pytest.raises(ValidationError):
        build_search_query(REPO, "repo:other/x is:pr OR", "all", "any")


def test_search_sends_scoped_query_and_drops_foreign_results(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {"/search/issues": (200, github_fixture("search_issues.json"), {})})
    result = gh.search_issues(REPO, "401 bearer repo:someone-else/other", kind="any")
    assert mock.requests[0].url.params["q"] == "401 bearer repo:octo-org/demo"
    assert [i.number for i in result.items] == [12, 15]  # #99 came from another repo
    assert result.items[1].is_pull_request and not result.items[0].is_pull_request
    assert result.items[0].labels == ["bug", "auth"]
    assert result.total_count == 3 and result.truncated


# ---- pagination -------------------------------------------------------------------------


def paged(n: int, link: str | None) -> tuple[int, list[dict], dict]:
    items = [{**github_fixture("pulls.json")[0], "number": n * 100 + i} for i in range(2)]
    return 200, items, ({"link": link} if link else {})


def test_pagination_follows_link_and_stops_at_max_pages(tmp_path: Path) -> None:
    next_link = f'<https://api.github.com{BASE}/pulls?state=open&per_page=2&page=2>; rel="next", <https://api.github.com{BASE}/pulls?page=9>; rel="last"'
    gh, mock = client(tmp_path, {f"{BASE}/pulls": [paged(1, next_link), paged(2, next_link), paged(3, next_link)]},
                      github_max_pages=2, github_max_results=50)
    result = gh.list_pull_requests(REPO, limit=50)
    assert len(mock.requests) == 2
    assert len(result.items) == 4 and result.truncated
    assert mock.requests[1].url.params["page"] == "2"


def test_pagination_stops_at_max_results(tmp_path: Path) -> None:
    next_link = f'<https://api.github.com{BASE}/pulls?page=2>; rel="next"'
    gh, mock = client(tmp_path, {f"{BASE}/pulls": [paged(1, next_link), paged(2, next_link)]},
                      github_max_results=3, github_max_pages=5)
    result = gh.list_pull_requests(REPO, limit=10)  # capped to GITHUB_MAX_RESULTS=3
    assert mock.requests[0].url.params["per_page"] == "3"
    assert len(result.items) == 3 and result.truncated


def test_pagination_without_next_is_complete(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {f"{BASE}/pulls": (200, github_fixture("pulls.json"), {})})
    result = gh.list_pull_requests(REPO)
    assert len(mock.requests) == 1 and not result.truncated and len(result.items) == 2


def test_pagination_never_follows_another_host(tmp_path: Path) -> None:
    evil = '<https://evil.example.com/steal?page=2>; rel="next"'
    gh, mock = client(tmp_path, {f"{BASE}/pulls": [paged(1, evil)]}, github_token=TOKEN)
    gh.list_pull_requests(REPO)
    assert len(mock.requests) == 1
    assert all(r.url.host == "api.github.com" for r in mock.requests)


# ---- rate limits ------------------------------------------------------------------------


def test_primary_rate_limit_403(tmp_path: Path) -> None:
    headers = {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1791003600"}
    gh, mock = client(tmp_path, {BASE: (403, github_fixture("rate_limited.json"), headers)})
    with pytest.raises(RateLimitError) as excinfo:
        gh.get_repository(REPO)
    err = excinfo.value
    assert err.reset_at is not None and int(err.reset_at.timestamp()) == 1791003600
    assert "resets at" in str(err) and "GITHUB_TOKEN" in str(err)
    assert len(mock.requests) == 1  # no waiting, no retry
    assert gh.last_rate_limit.remaining == 0


def test_secondary_rate_limit_429_with_retry_after(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {"/search/issues": (429, github_fixture("secondary_rate_limited.json"),
                                                    {"retry-after": "60"})}, github_token=TOKEN)
    with pytest.raises(RateLimitError) as excinfo:
        gh.search_issues(REPO, "token")
    assert excinfo.value.retry_after_seconds == 60
    assert "retry after 60s" in str(excinfo.value)
    assert "GITHUB_TOKEN" not in str(excinfo.value)  # already authenticated
    assert len(mock.requests) == 1


def test_secondary_rate_limit_403_by_message(tmp_path: Path) -> None:
    gh, _ = client(tmp_path, {BASE: (403, github_fixture("secondary_rate_limited.json"), {})})
    with pytest.raises(RateLimitError):
        gh.get_repository(REPO)


def test_rate_limit_status_on_success(tmp_path: Path) -> None:
    gh, _ = client(tmp_path, {BASE: (200, github_fixture("repo.json"), {})})
    repo = gh.get_repository(REPO)
    assert repo.rate_limit.remaining == 57 and repo.rate_limit.limit == 60
    assert repo.rate_limit.resource == "core" and repo.rate_limit.reset_at is not None


# ---- error mapping and retries -----------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (401, {"message": "Bad credentials"}, AuthError),
        (403, {"message": "Resource not accessible by personal access token"}, ForbiddenError),
        (404, {"message": "Not Found"}, NotFoundError),
        (410, {"message": "Gone"}, NotFoundError),
        (301, {"message": "Moved Permanently"}, NotFoundError),
        (422, github_fixture("error_422.json"), ValidationError),
    ],
)
def test_status_mapping(tmp_path: Path, status: int, body: dict, error: type) -> None:
    gh, mock = client(tmp_path, {BASE: (status, body, {"location": "https://api.github.com/repositories/1"})})
    with pytest.raises(error):
        gh.get_repository(REPO)
    assert len(mock.requests) == 1  # no retry, redirects not followed


def test_404_mentions_private_repos(tmp_path: Path) -> None:
    gh, _ = client(tmp_path, {})
    with pytest.raises(NotFoundError, match="private"):
        gh.get_repository(RepoRef("octo-org", "secret"))


def test_422_includes_github_reason(tmp_path: Path) -> None:
    gh, _ = client(tmp_path, {"/search/issues": (422, github_fixture("error_422.json"), {})})
    with pytest.raises(ValidationError, match="cannot be searched"):
        gh.search_issues(REPO, "x")


def test_5xx_is_retried_once_then_fails(tmp_path: Path) -> None:
    sleeps: list[float] = []
    gh, mock = client(tmp_path, {BASE: (502, {"message": "Bad Gateway"}, {})}, sleeps=sleeps)
    with pytest.raises(GitHubUnavailableError):
        gh.get_repository(REPO)
    assert len(mock.requests) == 2 and len(sleeps) == 1


def test_5xx_then_success(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {BASE: [(503, {}, {}), (200, github_fixture("repo.json"), {})]})
    assert gh.get_repository(REPO).full_name == "octo-org/demo"
    assert len(mock.requests) == 2


def test_timeout_is_retried_once(tmp_path: Path) -> None:
    timeout = httpx.ReadTimeout("slow")
    gh, mock = client(tmp_path, {BASE: [timeout, (200, github_fixture("repo.json"), {})]})
    assert gh.get_repository(REPO).stars == 42
    gh2, mock2 = client(tmp_path, {BASE: [timeout]})
    with pytest.raises(GitHubUnavailableError, match="in time"):
        gh2.get_repository(REPO)
    assert len(mock2.requests) == 2


def test_connection_error_is_not_retried(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {BASE: [httpx.ConnectError("no route")]})
    with pytest.raises(GitHubUnavailableError, match="ConnectError"):
        gh.get_repository(REPO)
    assert len(mock.requests) == 1


# ---- operations and normalization ---------------------------------------------------------


def test_get_repository_normalized(tmp_path: Path) -> None:
    gh, _ = client(tmp_path, {BASE: (200, github_fixture("repo.json"), {})})
    repo = gh.get_repository(REPO)
    assert (repo.default_branch, repo.stars, repo.forks, repo.open_issues_count) == ("main", 42, 7, 5)
    assert repo.license == "MIT" and repo.topics == ["api", "demo"] and repo.visibility == "public"


def test_get_issue_with_comments_and_truncation(tmp_path: Path) -> None:
    routes = {f"{BASE}/issues/12": (200, github_fixture("issue.json"), {}),
              f"{BASE}/issues/12/comments": (200, github_fixture("issue_comments.json"), {})}
    gh, mock = client(tmp_path, routes, github_max_body_chars=40, github_max_comments=2)
    issue = gh.get_issue(REPO, 12)
    assert not issue.is_pull_request and issue.note is None
    assert issue.body_truncated and len(issue.body) <= 41
    assert len(issue.comments) == 2 and issue.comments_truncated  # 4 comments exist
    assert issue.comments[0].author == "bob" and issue.comments[0].author_association == "MEMBER"
    assert mock.requests[1].url.params["per_page"] == "2"


def test_get_issue_without_comments_makes_one_request(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {f"{BASE}/issues/12": (200, github_fixture("issue.json"), {})})
    issue = gh.get_issue(REPO, 12, include_comments=False)
    assert issue.comments == [] and issue.comments_truncated and len(mock.requests) == 1


def test_get_issue_detects_pull_requests(tmp_path: Path) -> None:
    gh, _ = client(tmp_path, {f"{BASE}/issues/15": (200, github_fixture("issue_is_pr.json"), {})})
    issue = gh.get_issue(REPO, 15)
    assert issue.is_pull_request
    assert "get_pull_request" in issue.note


def test_invalid_numbers(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {})
    for bad in (0, -3):
        with pytest.raises(ValidationError):
            gh.get_issue(REPO, bad)
    assert mock.requests == []


def test_list_pull_requests(tmp_path: Path) -> None:
    gh, mock = client(tmp_path, {f"{BASE}/pulls": (200, github_fixture("pulls.json"), {})})
    prs = gh.list_pull_requests(REPO, state="closed")
    assert mock.requests[0].url.params["state"] == "closed"
    first, second = prs.items
    assert (first.base_branch, first.head_branch, first.merged_at is not None) == ("main", "fix-token", True)
    assert second.draft and second.merged_at is None


def test_get_pull_request_files_and_closing_refs(tmp_path: Path) -> None:
    routes = {f"{BASE}/pulls/15": (200, github_fixture("pull.json"), {}),
              f"{BASE}/pulls/15/files": (200, github_fixture("pull_files.json"), {})}
    gh, _ = client(tmp_path, routes)
    pr = gh.get_pull_request(REPO, 15)
    assert pr.merged and pr.changed_files == 2 and not pr.files_truncated
    assert [(f.filename, f.status, f.additions) for f in pr.files] == [
        ("app/auth.py", "modified", 8), ("tests/test_auth.py", "added", 2)]
    assert "patch" not in pr.files[0].model_dump()
    refs = [(r.keyword, r.repo, r.number) for r in pr.closing_references]
    assert refs == [("fixes", "octo-org/demo", 12), ("closes", "octo-org/other", 3), ("resolves", "octo-org/demo", 14)]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("Fixes #1", [("fixes", 1)]),
        ("closes: #2, resolved #3", [("closes", 2), ("resolved", 3)]),
        ("FIX #4", [("fix", 4)]),
        ("Related to #5. Mentions #6", []),
        ("prefix #7 and fixes#8", []),
        ("", []),
    ],
)
def test_parse_closing_references(body: str, expected: list[tuple[str, int]]) -> None:
    assert [(r.keyword, r.number) for r in parse_closing_references(body, REPO)] == expected


# ---- optional live tests (public data, read-only) --------------------------------------------


@pytest.mark.slow
def test_live_public_repository() -> None:
    """Reads one public repository. Uses GITHUB_TOKEN from .env automatically if set."""
    with GitHubClient(Settings(github_default_repo="fastapi/fastapi")) as gh:
        try:
            repo = gh.get_repository(RepoRef("fastapi", "fastapi"))
        except RateLimitError as exc:
            pytest.skip(f"GitHub rate limit reached: {exc}")
    assert repo.full_name.lower() == "fastapi/fastapi" and repo.stars > 0
    assert repo.rate_limit is not None and repo.rate_limit.remaining is not None
