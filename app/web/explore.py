"""Read-only routes for the explore pages: files, file viewer, git branch, search,
GitHub issues and pull requests, and the write audit log.

Nothing here writes. Every project path goes through the MCP allowlist
(``ProjectTools`` -> ``resolve_project``), every file through the ingestion rules
(``check_project_file``), and every repository through the GitHub read allowlist
(``GitHubTools`` -> ``resolve_repo``). GitHub and file text is untrusted: it is
returned unwrapped for display and the UI renders it as quoted external content.
"""

import re
import time
from collections import Counter
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from app.actions.audit import AuditLog
from app.config import Settings
from app.integrations.github_client import resolve_repo
from app.integrations.github_errors import GitHubError, GitHubNotConfiguredError, RepoNotAllowedError
from app.mcp_server.security import allowed_roots, resolve_project
from app.mcp_server.tools.project_tools import ProjectTools
from app.orchestration.assistant import unwrap_untrusted
from app.web import schemas
from app.web.sessions import SessionManager

_LINE = re.compile(r"^\s*(\d+) \| ?(.*)$")
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,200}$")
_SHA = re.compile(r"^[0-9a-f]{40,64}$")
GITHUB_STATUS_TTL = 60.0
MAX_AUDIT_ENTRIES = 200


def git_branch(project: Path) -> dict[str, Any] | None:
    """The checked-out branch, read from ``.git/HEAD`` (no git process, no shell).

    Returns None when the project isn't the root of a git repository.
    """
    head = project / ".git" / "HEAD"
    try:
        if head.is_symlink() or not head.is_file() or head.stat().st_size > 4096:
            return None
        text = head.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if text.startswith("ref: refs/heads/"):
        name = text.removeprefix("ref: refs/heads/")
        return {"branch": name, "detached": False} if _BRANCH.fullmatch(name) and ".." not in name else None
    if _SHA.fullmatch(text):
        return {"branch": text[:7], "detached": True}
    return None


def display_path(path: Path, roots: list[Path]) -> str:
    """``root-name/relative/path`` for display; the absolute path stays in tooltips only."""
    for root in roots:
        if path == root or path.is_relative_to(root):
            rel = path.relative_to(root)
            return root.name if str(rel) == "." else f"{root.name}/{rel}"
    return path.name


def _strip_line_numbers(content: str) -> tuple[list[str], int | None]:
    lines, first = [], None
    for raw in unwrap_untrusted(content).splitlines():
        match = _LINE.match(raw)
        if match:
            first = first or int(match.group(1))
            lines.append(match.group(2))
        else:
            lines.append(raw)
    return lines, first


def _rate(rate: Any) -> dict[str, Any] | None:
    return rate.model_dump(mode="json") if rate is not None else None


def _text(value: str | None) -> str:
    return unwrap_untrusted(value) if value else ""


def build_explore_router(sessions: SessionManager, http_error: Any) -> APIRouter:
    runtime = sessions.runtime
    settings: Settings = runtime.settings
    router = APIRouter(prefix="/api")
    github_status_cache: dict[str, tuple[float, dict[str, Any]]] = {}

    def tools() -> ProjectTools:
        return ProjectTools(settings, embedding_provider=runtime.embeddings)

    def guarded(fn: Any) -> Any:
        try:
            return fn()
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001 - mapped to a safe error
            raise http_error(exc) from None

    # ---- project ----------------------------------------------------------------------------

    @router.get("/project/files")
    def project_files(path: str = Query(min_length=1, max_length=4096)) -> dict[str, Any]:
        """Files the loader allows (the same ones indexing would read), marked if indexed."""
        def run() -> dict[str, Any]:
            project_tools = ProjectTools(settings)  # listing needs no embeddings
            result = project_tools.list_project_files(path)
            indexed: set[str] = set()
            try:
                indexed = set(project_tools.get_index_stats(path).files)
            except Exception:  # noqa: BLE001 - not indexed yet
                pass
            return {
                "project_name": result.project_name, "total_files": result.total_files, "truncated": result.truncated,
                "skipped_by_reason": result.skipped_by_reason,
                "file_types": dict(Counter(f.file_type for f in result.files).most_common()),
                "files": [{**f.model_dump(), "indexed": f.path in indexed} for f in result.files],
            }
        return guarded(run)

    @router.get("/project/file")
    def project_file(path: str = Query(min_length=1, max_length=4096),
                     file: str = Query(min_length=1, max_length=1024),
                     start: int | None = Query(default=None, ge=1)) -> dict[str, Any]:
        """A read-only view of one file, through ``read_project_file`` and its security rules."""
        def run() -> dict[str, Any]:
            result = ProjectTools(settings).read_project_file(path, file, start_line=start)
            lines, _ = _strip_line_numbers(result.content)
            return {"file_path": result.file_path, "start_line": result.start_line, "end_line": result.end_line,
                    "total_lines": result.total_lines, "truncated": result.truncated, "lines": lines,
                    "language": schemas.LANGUAGES.get(Path(result.file_path).suffix.lower())}
        return guarded(run)

    @router.get("/project/info")
    def project_info(path: str = Query(min_length=1, max_length=4096)) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            roots = allowed_roots(settings)
            project = resolve_project(path, roots)
            return {"path": str(project), "name": project.name, "display_path": display_path(project, roots),
                    "git": git_branch(project)}
        return guarded(run)

    @router.get("/project/search")
    def project_search(path: str = Query(min_length=1, max_length=4096),
                       q: str = Query(min_length=1, max_length=500),
                       top_k: int = Query(default=6, ge=1, le=20)) -> dict[str, Any]:
        """Semantic search over the project index (no LLM)."""
        def run() -> dict[str, Any]:
            project_tools = tools()
            project = resolve_project(path, project_tools.roots)
            result = project_tools.search_project(path, q, top_k=min(top_k, settings.mcp_max_top_k))
            hits = []
            for hit in result.hits:
                start, end, partial = schemas.line_info(settings.model_copy(update={"project_root": project}),
                                                        project, hit.file_path, hit.location)
                excerpt, start = schemas.trim_partial_line(_text(hit.content), start, end, partial)
                hits.append({"file_path": hit.file_path, "score": hit.score, "start_line": start, "end_line": end,
                             "excerpt": (excerpt or "")[:600],
                             "language": schemas.LANGUAGES.get(Path(hit.file_path).suffix.lower())})
            return {"query": result.query, "hits": hits}
        return guarded(run)

    # ---- GitHub (read-only, allowlisted) --------------------------------------------------------

    @router.get("/github/status")
    def github_status(repo: str | None = Query(default=None, max_length=140)) -> dict[str, Any]:
        """Configured + reachable check for the selected repo. Cached for a minute."""
        key = repo or ""
        cached = github_status_cache.get(key)
        if cached and time.monotonic() - cached[0] < GITHUB_STATUS_TTL:
            return cached[1]
        authenticated = settings.github_token is not None
        try:
            ref = resolve_repo(settings, repo)
        except (GitHubNotConfiguredError, RepoNotAllowedError) as exc:
            return {"configured": False, "reachable": False, "authenticated": authenticated, "repo": repo,
                    "message": str(exc), "rate_limit": None}
        try:
            info = runtime.github.get_repository(ref.full_name)
            out = {"configured": True, "reachable": True, "authenticated": authenticated, "repo": ref.full_name,
                   "message": None, "rate_limit": _rate(info.rate_limit),
                   "repository": {"description": _text(info.description), "default_branch": info.default_branch,
                                  "stars": info.stars, "open_issues_count": info.open_issues_count,
                                  "html_url": info.html_url, "visibility": info.visibility}}
        except GitHubError as exc:
            out = {"configured": True, "reachable": False, "authenticated": authenticated, "repo": ref.full_name,
                   "message": http_error(exc).detail["message"], "rate_limit": None}
        github_status_cache[key] = (time.monotonic(), out)
        return out

    def issue_summary(item: Any) -> dict[str, Any]:
        data = item.model_dump(mode="json")
        data["title"] = _text(item.title)
        data["body_excerpt"] = _text(item.body_excerpt)
        return data

    @router.get("/github/issues")
    def github_issues(repo: str | None = Query(default=None, max_length=140),
                      q: str = Query(default="", max_length=256),
                      state: Literal["open", "closed", "all"] = "all",
                      kind: Literal["issue", "pr", "any"] = "issue",
                      limit: int = Query(default=20, ge=1, le=50)) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            if q.strip():
                result = runtime.github.search_issues(repo, q, state=state, kind=kind, limit=limit)
                return {"repo": result.repo, "query": q, "total_count": result.total_count,
                        "items": [issue_summary(i) for i in result.items], "rate_limit": _rate(result.rate_limit)}
            ref = resolve_repo(settings, repo)
            client = runtime.github.client
            items = client.list_issues(ref, state=state, limit=limit, sort="updated")
            return {"repo": ref.full_name, "query": "", "total_count": len(items),
                    "items": [issue_summary(i) for i in items], "rate_limit": _rate(client.last_rate_limit)}
        return guarded(run)

    @router.get("/github/issues/{number}")
    def github_issue(number: int, repo: str | None = Query(default=None, max_length=140)) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            issue = runtime.github.get_issue(repo, number, include_comments=True)
            data = issue.model_dump(mode="json")
            data["title"], data["body"] = _text(issue.title), _text(issue.body)
            for comment in data["comments"]:
                comment["body"] = _text(comment["body"])
            return data
        return guarded(run)

    @router.get("/github/pulls")
    def github_pulls(repo: str | None = Query(default=None, max_length=140),
                     state: Literal["open", "closed", "all"] = "open",
                     limit: int = Query(default=20, ge=1, le=50)) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            result = runtime.github.list_pull_requests(repo, state=state, limit=limit)
            data = result.model_dump(mode="json")
            for item in data["items"]:
                item["title"] = _text(item["title"])
            return data
        return guarded(run)

    @router.get("/github/pulls/{number}")
    def github_pull(number: int, repo: str | None = Query(default=None, max_length=140)) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            pr = runtime.github.get_pull_request(repo, number)
            data = pr.model_dump(mode="json")
            data["title"], data["body"] = _text(pr.title), _text(pr.body)
            return data
        return guarded(run)

    # ---- audit log ----------------------------------------------------------------------------

    @router.get("/activity/audit")
    def audit() -> dict[str, Any]:
        """Issue-creation audit entries (metadata only: never bodies, titles or tokens)."""
        def run() -> dict[str, Any]:
            try:
                records = AuditLog(settings.audit_log_path).read()
            except Exception:  # noqa: BLE001 - unreadable log: report, don't crash
                return {"entries": [], "error": "The audit log could not be read."}
            entries = [r.model_dump(include={"timestamp", "action", "repo", "channel", "issue_number", "issue_url",
                                             "error_category", "title_length"}) | {"draft_hash": r.draft_hash[:12]}
                       for r in records[-MAX_AUDIT_ENTRIES:]]
            return {"entries": list(reversed(entries)), "error": None}
        return guarded(run)

    return router
