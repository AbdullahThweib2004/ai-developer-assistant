"""Web API: line ranges on sources and the read-only explore endpoints (files, viewer,
git branch, search, GitHub issues/PRs, audit log). Fakes only, no network."""

from pathlib import Path

import pytest

from app.actions.audit import AuditLog
from app.config import Settings
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.indexer import build_index
from app.web import schemas
from app.web.explore import display_path, git_branch
from tests.helpers import READ_TOKEN, github_fixture, write_settings
from tests.test_web_api import ISSUES, QUESTION, SEARCH, Harness, issues_endpoint

READS = {
    ISSUES: issues_endpoint(),
    "/repos/octo-org/demo": (200, github_fixture("repo.json"), {"x-ratelimit-limit": "5000", "x-ratelimit-remaining": "4990"}),
    "/repos/octo-org/demo/issues": (200, [github_fixture("issue.json")], {}),
    "/repos/octo-org/demo/issues/12": (200, github_fixture("issue.json"), {}),
    "/repos/octo-org/demo/issues/12/comments": (200, github_fixture("issue_comments.json"), {}),
    "/search/issues": (200, github_fixture("search_issues.json"), {}),
    "/repos/octo-org/demo/pulls": (200, github_fixture("pulls.json"), {}),
    "/repos/octo-org/demo/pulls/15": (200, github_fixture("pull.json"), {}),
    "/repos/octo-org/demo/pulls/15/files": (200, github_fixture("pull_files.json"), {}),
}


@pytest.fixture
def settings(index_settings: Settings, tmp_path: Path) -> Settings:
    root = index_settings.project_root
    return write_settings(root, audit_log_path=tmp_path / "audit" / "github_writes.jsonl", embedding_provider="fake",
                          chroma_persist_dir=index_settings.chroma_persist_dir, mcp_allowed_project_roots=str(root),
                          chunk_size=300, chunk_overlap=50)


@pytest.fixture
def indexed(settings: Settings) -> Settings:
    build_index(settings.project_root, settings=settings, provider=FakeEmbeddingProvider())
    return settings


def harness(settings: Settings, script: list | None = None) -> Harness:
    return Harness(settings, script=script, routes=READS)


# ---- line ranges --------------------------------------------------------------------------------


def test_line_info_converts_char_offsets_and_detects_partial_lines(tmp_path: Path, settings: Settings) -> None:
    project = settings.project_root
    (project / "lines.py").write_text("a = 1\nb = 2\nc = 3\nd = 4\n")
    s = settings.model_copy(update={"project_root": project})
    assert schemas.line_info(s, project, "lines.py", "chars 0-11") == (1, 2, False)
    assert schemas.line_info(s, project, "lines.py", "chars 6-24") == (2, 4, False)
    assert schemas.line_info(s, project, "lines.py", "chars 8-17") == (2, 3, True)  # starts mid-line
    assert schemas.line_info(s, project, "lines.py", "lines 3-9") == (3, 9, False)
    assert schemas.trim_partial_line("= 2\nc = 3", 2, 3, True) == ("c = 3", 3)
    assert schemas.trim_partial_line("= 2\nc = 3", 2, 3, False) == ("= 2\nc = 3", 2)


def test_line_info_never_reads_files_the_loader_refuses(settings: Settings) -> None:
    project = settings.project_root
    s = settings.model_copy(update={"project_root": project})
    assert schemas.line_info(s, project, ".env", "chars 0-10") == (None, None, False)
    assert schemas.line_info(s, project, "../outside.py", "chars 0-10") == (None, None, False)


def test_investigation_sources_carry_start_and_end_lines(indexed: Settings) -> None:
    h = harness(indexed, script=[SEARCH, "It hashes the password [S1]."])
    events = h.investigate(h.conversation(indexed.project_root), QUESTION)
    sources = next(d for n, d in events if n == "answer")["project_sources"]
    assert sources
    for src in sources:
        assert 1 <= src["start_line"] <= src["end_line"]
        lines = (indexed.project_root / src["file_path"]).read_text().splitlines()
        assert src["end_line"] <= len(lines)
        first = (src["excerpt"] or "").splitlines()[0].strip()
        if first:  # the excerpt starts at a whole line: the line at start_line contains it
            assert first in lines[src["start_line"] - 1]


def test_quick_answer_sources_carry_line_ranges(indexed: Settings) -> None:
    h = harness(indexed)
    h.llm.script = ["It checks the hash [S1]."]
    events = h.stream("/api/ask", {"path": str(indexed.project_root), "question": QUESTION,
                                   "conversation_id": h.conversation(indexed.project_root)["id"]})
    sources = events[-1][1]["sources"]
    assert sources and all(1 <= s["start_line"] <= s["end_line"] for s in sources)


# ---- project explore endpoints ---------------------------------------------------------------------


def test_project_files_lists_only_loader_allowed_files(indexed: Settings) -> None:
    data = harness(indexed).get("/api/project/files", params={"path": str(indexed.project_root)}).json()
    paths = {f["path"] for f in data["files"]}
    assert "auth.py" in paths and ".env" not in paths
    assert not any(p.startswith(("node_modules", ".venv")) for p in paths)
    assert all(f["indexed"] for f in data["files"] if f["path"] == "auth.py")
    assert data["skipped_by_reason"] and all(isinstance(v, int) for v in data["skipped_by_reason"].values())
    assert data["file_types"]["py"] >= 1


def test_project_file_viewer_reads_through_the_security_rules(settings: Settings) -> None:
    h = harness(settings)
    root = str(settings.project_root)
    ok = h.get("/api/project/file", params={"path": root, "file": "auth.py", "start": 2})
    assert ok.status_code == 200
    body = ok.json()
    assert body["start_line"] == 2 and body["lines"][0] == (settings.project_root / "auth.py").read_text().splitlines()[1]
    assert "untrusted" not in "".join(body["lines"])  # wrapper tags stripped for display
    for bad in [".env", "../outside.py", "/etc/passwd", "node_modules/pkg/index.js"]:
        response = h.get("/api/project/file", params={"path": root, "file": bad})
        assert response.status_code in (400, 403, 404), bad
        assert "DUMMY_SECRET" not in response.text


def test_explore_endpoints_reject_projects_outside_the_allowlist(settings: Settings, tmp_path: Path) -> None:
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    h = harness(settings)
    for url in ["/api/project/files", "/api/project/info", "/api/project/file"]:
        response = h.get(url, params={"path": str(outside), "file": "x.py"})
        assert response.status_code == 403
        assert str(outside) not in response.text


def test_explore_endpoints_need_the_session_token(settings: Settings) -> None:
    h = harness(settings)
    for url in ["/api/project/files", "/api/project/info", "/api/github/status", "/api/github/issues", "/api/activity/audit"]:
        assert h.client.get(url, params={"path": str(settings.project_root)}).status_code == 401


def test_git_branch_read_from_head_without_running_git(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    head = tmp_path / ".git" / "HEAD"
    head.write_text("ref: refs/heads/feature/login-fix\n")
    assert git_branch(tmp_path) == {"branch": "feature/login-fix", "detached": False}
    head.write_text("a" * 40 + "\n")
    assert git_branch(tmp_path) == {"branch": "aaaaaaa", "detached": True}
    head.write_text("ref: refs/heads/$(rm -rf /)\n")
    assert git_branch(tmp_path) is None  # anything unexpected is ignored
    assert git_branch(tmp_path / "nope") is None


def test_project_info_shows_relative_display_path(settings: Settings) -> None:
    info = harness(settings).get("/api/project/info", params={"path": str(settings.project_root)}).json()
    assert info["display_path"] == settings.project_root.name and info["git"] is None
    root = Path("/srv/code")
    assert display_path(root / "team" / "api", [root]) == "code/team/api"


def test_project_search_returns_hits_with_line_ranges(indexed: Settings) -> None:
    data = harness(indexed).get("/api/project/search", params={"path": str(indexed.project_root), "q": "password hash"}).json()
    assert data["hits"]
    assert all(h["start_line"] and h["end_line"] >= h["start_line"] for h in data["hits"])


def test_projects_list_has_display_path_and_file_types(indexed: Settings) -> None:
    projects = harness(indexed).get("/api/projects").json()["projects"]
    project = next(p for p in projects if p["path"] == str(indexed.project_root))
    assert project["display_path"] and project["file_types"]["py"] >= 1


# ---- GitHub (read-only) -------------------------------------------------------------------------------


def test_github_status_reports_reachability_and_rate_limit_without_secrets(settings: Settings) -> None:
    h = harness(settings)
    data = h.get("/api/github/status", params={"repo": "octo-org/demo"}).json()
    assert data["configured"] and data["reachable"] and data["authenticated"]
    assert data["rate_limit"]["remaining"] == 4990
    assert READ_TOKEN not in str(data)
    assert not h.get("/api/github/status", params={"repo": "someone/else"}).json()["configured"]


def test_github_reads_enforce_the_allowlist(settings: Settings) -> None:
    h = harness(settings)
    for url in ["/api/github/issues", "/api/github/issues/12", "/api/github/pulls", "/api/github/pulls/15"]:
        assert h.get(url, params={"repo": "someone/else"}).status_code == 400
    assert not h.github.requests  # refused before any request


def test_github_issue_list_search_and_detail(settings: Settings) -> None:
    h = harness(settings)
    listed = h.get("/api/github/issues", params={"repo": "octo-org/demo"}).json()
    assert listed["items"] and listed["query"] == ""
    found = h.get("/api/github/issues", params={"repo": "octo-org/demo", "q": "login"}).json()
    assert found["items"]
    detail = h.get("/api/github/issues/12", params={"repo": "octo-org/demo"}).json()
    assert detail["number"] == 12 and "<untrusted" not in detail["body"]
    assert all("<untrusted" not in c["body"] for c in detail["comments"])


def test_github_pulls_list_and_detail_with_changed_files(settings: Settings) -> None:
    h = harness(settings)
    assert h.get("/api/github/pulls", params={"repo": "octo-org/demo"}).json()["items"]
    pr = h.get("/api/github/pulls/15", params={"repo": "octo-org/demo"}).json()
    assert pr["number"] == 15 and pr["files"] and "<untrusted" not in pr["title"]
    assert all(r.method == "GET" for r in h.github.requests)


# ---- audit log ---------------------------------------------------------------------------------------


def test_audit_endpoint_returns_metadata_only(settings: Settings) -> None:
    log = AuditLog(settings.audit_log_path)
    log.record("cancelled", "octo-org/sandbox", "f" * 64, 42, "web")
    log.record("created", "octo-org/sandbox", "e" * 64, 30, "web", issue_number=7, issue_url="https://github.com/octo-org/sandbox/issues/7")
    entries = harness(settings).get("/api/activity/audit").json()["entries"]
    assert [e["action"] for e in entries] == ["created", "cancelled"]  # newest first
    assert entries[0]["draft_hash"] == "e" * 12  # shortened
    assert set(entries[0]) <= {"timestamp", "action", "repo", "channel", "issue_number", "issue_url", "error_category",
                               "title_length", "draft_hash"}


def test_audit_endpoint_with_no_log(settings: Settings) -> None:
    assert harness(settings).get("/api/activity/audit").json() == {"entries": [], "error": None}
