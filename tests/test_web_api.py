"""Web API tests (FastAPI TestClient, fakes only, no network)."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.actions.approval import IssueApprovalService
from app.config import Settings
from app.integrations.github_client import GitHubClient
from app.integrations.github_write_client import GitHubWriteClient
from app.llm.fake_provider import FakeLLMProvider, FakeToolCalls
from app.mcp_server.tools.github_tools import GitHubTools
from app.orchestration.tool_backends import InProcessBackend
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.indexer import build_index
from app.web.demo import DemoLLM, demo_runtime
from app.web.security import LocalSecurity
from app.web.server import REPO_ROOT, create_app, main
from app.web.sessions import Runtime
from tests.helpers import READ_TOKEN, SANDBOX, WRITE_TOKEN, GitHubMock, github_fixture, issues_endpoint, write_settings

BASE = "http://127.0.0.1:8765"
ISSUES = f"/repos/{SANDBOX}/issues"
QUESTION = "How does verify_user_credentials check a password?"


def sse_events(response: Any) -> list[tuple[str, Any]]:
    events, name = [], None
    for line in response.iter_lines():
        if line.startswith("event:"):
            name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            events.append((name, json.loads(line[5:])))
    return events


class Harness:
    """A test app over the fixture project, with a scripted LLM and mocked GitHub."""

    def __init__(self, settings: Settings, script: list | None = None, routes: dict | None = None) -> None:
        self.settings = settings
        self.github = GitHubMock(routes or {ISSUES: issues_endpoint(),
                                            "/repos/octo-org/demo/issues/12": (200, github_fixture("issue.json"), {}),
                                            "/repos/octo-org/demo/issues/12/comments": (200, github_fixture("issue_comments.json"), {})})
        self.llm = FakeLLMProvider(script=script or [])
        transport = self.github.transport
        runtime = Runtime(
            settings=settings, make_llm=lambda: self.llm,  # type: ignore[arg-type,return-value]
            make_backend=lambda project: InProcessBackend(settings, project, FakeEmbeddingProvider(),
                                                          github_transport=transport),
            make_approvals=lambda project: IssueApprovalService(
                settings, channel="web-test", project_root=project,
                write_client_factory=lambda: GitHubWriteClient(
                    settings, transport=transport, read_client_factory=lambda: GitHubClient(settings, transport=transport)),
                read_client_factory=lambda: GitHubClient(settings, transport=transport)),
            make_embeddings=FakeEmbeddingProvider,
            make_github=lambda: GitHubTools(settings, transport=transport))
        self.security = LocalSecurity(port=8765)
        self.client = TestClient(create_app(runtime, self.security), base_url=BASE)
        self.headers = {"X-Session-Token": self.security.token}

    def get(self, url: str, **kw: Any) -> Any:
        return self.client.get(url, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def post(self, url: str, **kw: Any) -> Any:
        return self.client.post(url, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def put(self, url: str, **kw: Any) -> Any:
        return self.client.put(url, headers={**self.headers, **kw.pop("headers", {})}, **kw)

    def stream(self, url: str, body: dict) -> list[tuple[str, Any]]:
        with self.client.stream("POST", url, json=body, headers=self.headers) as response:
            assert response.status_code == 200, response.read()
            return sse_events(response)

    def posts(self) -> list:
        return [r for r in self.github.requests if r.method == "POST"]


@pytest.fixture
def settings(index_settings: Settings, tmp_path: Path) -> Settings:
    root = index_settings.project_root
    return write_settings(root, audit_log_path=tmp_path / "audit" / "github_writes.jsonl", embedding_provider="fake",
                          chroma_persist_dir=index_settings.chroma_persist_dir,
                          mcp_allowed_project_roots=str(root), orch_memory_turns=2)


@pytest.fixture
def indexed(settings: Settings) -> Settings:
    build_index(settings.project_root, settings=settings, provider=FakeEmbeddingProvider())
    return settings


SEARCH = FakeToolCalls([("search_project", {"query": "verify user credentials password", "top_k": 3})])


# ---- local security ---------------------------------------------------------------------------


def test_token_required_on_every_api_route(settings: Settings) -> None:
    h = Harness(settings)
    for method, url in [("GET", "/api/status"), ("GET", "/api/projects"), ("POST", "/api/sessions")]:
        assert h.client.request(method, url).status_code == 401
        assert h.client.request(method, url, headers={"X-Session-Token": "wrong"}).status_code == 401
    assert h.get("/api/status").status_code == 200


@pytest.mark.parametrize("host", ["evil.example.com", "evil.example.com:8765", "127.0.0.1:9999", "192.168.1.5:8765"])
def test_host_header_checked_against_dns_rebinding(settings: Settings, host: str) -> None:
    h = Harness(settings)
    assert h.get("/api/status", headers={"Host": host}).status_code == 421


@pytest.mark.parametrize("headers", [{"Origin": "https://evil.example.com"}, {"Origin": "http://localhost:5173"},
                                     {"Sec-Fetch-Site": "cross-site"}])
def test_cross_origin_requests_refused(settings: Settings, headers: dict) -> None:
    h = Harness(settings)
    assert h.post("/api/sessions", json={"path": str(settings.project_root)}, headers=headers).status_code == 403
    assert h.get("/api/status", headers=headers).status_code == 403
    assert "access-control-allow-origin" not in {k.lower() for k in h.get("/api/status").headers}


def test_same_origin_allowed(settings: Settings) -> None:
    h = Harness(settings)
    assert h.get("/api/status", headers={"Origin": BASE, "Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_refuses_network_bind_without_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--host", "0.0.0.0"])
    assert exc.value.code == 2 and "--allow-network" in capsys.readouterr().err


def test_page_gets_the_token_and_missing_build_is_explained(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings)
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><head><title>x</title></head><body></body></html>")
    app = create_app(h.client.app.state.sessions.runtime, h.security, frontend_dist=dist)  # type: ignore[attr-defined]
    page = TestClient(app, base_url=BASE).get("/").text
    assert f'content="{h.security.token}"' in page
    missing = create_app(h.client.app.state.sessions.runtime, h.security, frontend_dist=tmp_path / "nope")  # type: ignore[attr-defined]
    response = TestClient(missing, base_url=BASE).get("/")
    assert response.status_code == 503 and "npm run build" in response.text


# ---- status / projects / errors ------------------------------------------------------------------


def test_status_never_contains_secrets(settings: Settings) -> None:
    secret_settings = settings.model_copy(update={"openai_api_key": "sk-test-not-real-0123456789"})
    raw = Harness(secret_settings).get("/api/status").text
    for secret in (READ_TOKEN, WRITE_TOKEN, "sk-test-not-real-0123456789"):
        assert secret not in raw
    data = json.loads(raw)
    assert data["github"]["write_enabled"] is True and data["github"]["write_repos"] == [SANDBOX]
    assert "token" not in json.dumps(data["github"]).replace("authenticated_reads", "")


def test_projects_lists_indexed_projects(indexed: Settings) -> None:
    projects = Harness(indexed).get("/api/projects").json()["projects"]
    entry = next(p for p in projects if p["path"] == str(indexed.project_root.resolve()))
    assert entry["indexed"] and entry["chunks"] > 0 and entry["files"] > 0


def test_paths_outside_allowed_roots_are_rejected_without_leaking(settings: Settings, tmp_path: Path) -> None:
    h = Harness(settings)
    outside = tmp_path / "private-outside-dir"
    outside.mkdir()
    for url, body in [("/api/index", {"path": str(outside)}), ("/api/sessions", {"path": str(outside)}),
                      ("/api/ask", {"path": str(outside), "question": "x"}),
                      ("/api/index", {"path": f"{settings.project_root}/../private-outside-dir"})]:
        response = h.post(url, json=body)
        assert response.status_code == 403
        assert str(outside) not in response.text and "Traceback" not in response.text


def test_unexpected_errors_are_generic(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    h = Harness(settings)
    monkeypatch.setattr("app.web.api.index_stats", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("secret /x/y")))
    response = h.get("/api/projects")
    assert response.status_code == 200  # projects skips unreadable entries
    monkeypatch.setattr("app.web.api.allowed_repos", lambda *a: (_ for _ in ()).throw(RuntimeError("boom /x/y")))
    client = TestClient(h.client.app, base_url=BASE, raise_server_exceptions=False)  # what a browser would see
    response = client.get("/api/status", headers=h.headers)
    assert response.status_code == 500 and response.json()["detail"]["message"] == "Something went wrong."
    assert "/x/y" not in response.text and "Traceback" not in response.text


# ---- index / ask ---------------------------------------------------------------------------------


def test_index_streams_progress(settings: Settings) -> None:
    events = Harness(settings).stream("/api/index", {"path": str(settings.project_root)})
    names = [n for n, _ in events]
    assert names[0] == "started" and names[-1] == "done"
    assert [e["type"] for n, e in events if n == "progress"][:3] == ["discovered", "parsed", "chunked"]
    assert events[-1][1]["chunks_indexed"] > 0


def test_ask_streams_status_then_answer(indexed: Settings) -> None:
    h = Harness(indexed)
    events = h.stream("/api/ask", {"path": str(indexed.project_root), "question": QUESTION})
    assert [n for n, _ in events] == ["status", "status", "answer"]
    answer = events[-1][1]
    assert answer["answer_markdown"] and answer["sources"] and "sections" in answer


def test_ask_not_indexed_is_a_friendly_error_event(settings: Settings) -> None:
    events = Harness(settings).stream("/api/ask", {"path": str(settings.project_root), "question": QUESTION})
    assert events[-1][0] == "error" and events[-1][1]["category"] == "not_indexed"
    assert "Index" in events[-1][1]["hint"]


# ---- sessions / investigate --------------------------------------------------------------------------


def test_session_lifecycle_and_validation(indexed: Settings) -> None:
    h = Harness(indexed)
    session = h.post("/api/sessions", json={"path": str(indexed.project_root), "mode": "project"}).json()
    assert session["mode"] == "project" and "search_project" in session["tools"]
    assert not any("github" in t for t in session["tools"])
    assert h.get(f"/api/sessions/{session['id']}").json()["id"] == session["id"]
    assert h.client.delete(f"/api/sessions/{session['id']}", headers=h.headers).json() == {"deleted": True}
    assert h.get(f"/api/sessions/{session['id']}").status_code == 404
    bad_repo = h.post("/api/sessions", json={"path": str(indexed.project_root), "repo": "evil/repo"})
    assert bad_repo.status_code == 400
    no_repo = indexed.model_copy(update={"github_default_repo": None, "github_allowed_repos": ""})
    assert Harness(no_repo).post("/api/sessions", json={"path": str(indexed.project_root),
                                                        "mode": "github"}).status_code == 400


def test_investigate_event_order_schema_and_memory(indexed: Settings) -> None:
    h = Harness(indexed, script=[SEARCH, "It compares hashes [S1].", "Follow-up answer [S1]."])
    session = h.post("/api/sessions", json={"path": str(indexed.project_root), "mode": "project"}).json()
    events = h.stream("/api/investigate", {"session_id": session["id"], "question": QUESTION})
    names = [n for n, _ in events]
    assert names == ["started", "llm_call", "step_started", "step_finished", "llm_call", "answer", "done"]
    started = dict(events)["step_started"]
    assert started["name"] == "search_project" and started["args"]["query"]
    finished = dict(events)["step_finished"]
    assert finished["status"] == "ok" and finished["result_count"] == 3 and finished["duration_ms"] >= 0
    answer = dict(events)["answer"]
    assert answer["project_sources"][0]["cited"] and answer["project_sources"][0]["file_path"] == "auth.py"
    assert answer["project_sources"][0]["start_line"] >= 1 and answer["invalid_citations"] == []
    assert all("compare_digest" not in json.dumps(e) for n, e in events if n in ("step_started", "step_finished"))

    h.stream("/api/investigate", {"session_id": session["id"], "question": "And the follow-up?"})
    second_prompt = h.llm.chat_calls[-1].messages[0].content
    assert "previous_conversation" in second_prompt and QUESTION in second_prompt
    assert h.get(f"/api/sessions/{session['id']}").json()["memory_turns"] == 2


def test_investigate_busy_session(indexed: Settings) -> None:
    h = Harness(indexed, script=["done"])
    session = h.post("/api/sessions", json={"path": str(indexed.project_root), "mode": "project"}).json()
    web_session = h.client.app.state.sessions.get(session["id"])  # type: ignore[attr-defined]
    web_session.lock.acquire()
    response = h.post("/api/investigate", json={"session_id": session["id"], "question": "q"})
    assert response.status_code == 409 and response.json()["detail"]["category"] == "busy"


# ---- drafts: Phase 7 guarantees through the API --------------------------------------------------------


def answered_session(h: Harness, project: Path) -> tuple[str, str]:
    session = h.post("/api/sessions", json={"path": str(project), "mode": "project"}).json()
    events = h.stream("/api/investigate", {"session_id": session["id"], "question": QUESTION})
    return session["id"], dict(events)["done"]["turn_id"]


def prepare(h: Harness, project: Path) -> dict:
    session_id, turn_id = answered_session(h, project)
    response = h.post(f"/api/sessions/{session_id}/drafts", json={"turn_id": turn_id})
    assert response.status_code == 200, response.text
    return response.json()


def test_draft_requires_write_enabled(indexed: Settings) -> None:
    h = Harness(indexed.model_copy(update={"github_write_enabled": False}), script=[SEARCH, "Answer [S1]."])
    session_id, turn_id = answered_session(h, indexed.project_root)
    response = h.post(f"/api/sessions/{session_id}/drafts", json={"turn_id": turn_id})
    assert response.status_code == 400 and response.json()["detail"]["category"] == "write_disabled"
    assert "GITHUB_WRITE_ENABLED" in response.json()["detail"]["hint"]


def test_approve_requires_exact_typed_repo_and_creates_once(indexed: Settings) -> None:
    h = Harness(indexed, script=[SEARCH, "It compares hashes [S1]."])
    draft = prepare(h, indexed.project_root)
    assert draft["repo"] == SANDBOX and draft["errors"] == []
    for typed in ("yes", "approve", SANDBOX.upper(), ""):
        response = h.post(f"/api/drafts/{draft['id']}/approve",
                          json={"draft_hash": draft["draft_hash"], "typed_repo_confirmation": typed})
        assert response.status_code == 409 and "Cancelled" in response.json()["detail"]["message"]
    assert h.posts() == []

    ok = h.post(f"/api/drafts/{draft['id']}/approve",
                json={"draft_hash": draft["draft_hash"], "typed_repo_confirmation": SANDBOX})
    assert ok.status_code == 200 and ok.json()["result"]["status"] == "created" and ok.json()["result"]["number"] == 7
    assert len(h.posts()) == 1
    again = h.post(f"/api/drafts/{draft['id']}/approve",
                   json={"draft_hash": draft["draft_hash"], "typed_repo_confirmation": SANDBOX})
    assert again.status_code == 409 and len(h.posts()) == 1  # one attempt per draft
    assert h.get(f"/api/drafts/{draft['id']}").json()["attempted"] is True


def test_edit_changes_hash_and_old_hash_is_refused(indexed: Settings) -> None:
    h = Harness(indexed, script=[SEARCH, "It compares hashes [S1]."])
    draft = prepare(h, indexed.project_root)
    edited = h.put(f"/api/drafts/{draft['id']}", json={"title": draft["title"] + " (edited)", "body": draft["body"],
                                                       "labels": []}).json()
    assert edited["draft_hash"] != draft["draft_hash"]
    stale = h.post(f"/api/drafts/{draft['id']}/approve",
                   json={"draft_hash": draft["draft_hash"], "typed_repo_confirmation": SANDBOX})
    assert stale.status_code == 409 and stale.json()["detail"]["category"] == "draft_changed"
    assert h.posts() == []
    fresh = h.post(f"/api/drafts/{draft['id']}/approve",
                   json={"draft_hash": edited["draft_hash"], "typed_repo_confirmation": SANDBOX})
    assert fresh.json()["result"]["status"] == "created"


def test_edit_voids_existing_approvals_in_the_service(indexed: Settings) -> None:
    h = Harness(indexed, script=[SEARCH, "It compares hashes [S1]."])
    draft = prepare(h, indexed.project_root)
    session, _ = h.client.app.state.sessions.find_draft(draft["id"])  # type: ignore[attr-defined]
    approval = session.approvals.approve(draft["id"], SANDBOX)  # e.g. an approval made elsewhere
    h.put(f"/api/drafts/{draft['id']}", json={"title": "Changed", "body": draft["body"], "labels": []})
    assert session.approvals.approvals[approval.approval_id].used is True  # voided by the edit


def test_secret_in_edit_blocks_approval(indexed: Settings) -> None:
    h = Harness(indexed, script=[SEARCH, "It compares hashes [S1]."])
    draft = prepare(h, indexed.project_root)
    leaked = "token " + "gh" + "p_" + "Ab3" * 12
    edited = h.put(f"/api/drafts/{draft['id']}", json={"title": "t", "body": leaked, "labels": []}).json()
    assert any("GitHub token" in e for e in edited["errors"]) and "Ab3Ab3" not in json.dumps(edited["errors"])
    response = h.post(f"/api/drafts/{draft['id']}/approve",
                      json={"draft_hash": edited["draft_hash"], "typed_repo_confirmation": SANDBOX})
    assert response.status_code == 409 and h.posts() == []


def test_similar_issues_need_second_confirmation(indexed: Settings) -> None:
    listing = [{"number": 4, "title": "How does verify_user_credentials check a password?", "state": "open",
                "html_url": f"https://github.com/{SANDBOX}/issues/4", "user": {"login": "x"}, "labels": [],
                "comments": 0, "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-01T00:00:00Z", "body": ""}]
    h = Harness(indexed, script=[SEARCH, "It compares hashes [S1]."], routes={ISSUES: issues_endpoint(listing=listing)})
    draft = prepare(h, indexed.project_root)
    assert h.get(f"/api/drafts/{draft['id']}/similar").json()["similar"][0]["number"] == 4
    first = h.post(f"/api/drafts/{draft['id']}/approve",
                   json={"draft_hash": draft["draft_hash"], "typed_repo_confirmation": SANDBOX}).json()
    assert first["result"]["status"] == "needs_confirmation" and h.posts() == []
    second = h.post(f"/api/drafts/{draft['id']}/approve",
                    json={"draft_hash": draft["draft_hash"], "typed_repo_confirmation": SANDBOX,
                          "confirm_similar": True}).json()
    assert second["result"]["status"] == "created" and len(h.posts()) == 1


def test_write_disabled_after_draft_is_refused(indexed: Settings) -> None:
    h = Harness(indexed, script=[SEARCH, "It compares hashes [S1]."])
    draft = prepare(h, indexed.project_root)
    session, _ = h.client.app.state.sessions.find_draft(draft["id"])  # type: ignore[attr-defined]
    session.approvals.settings = session.approvals.settings.model_copy(update={"github_write_enabled": False})
    response = h.post(f"/api/drafts/{draft['id']}/approve",
                      json={"draft_hash": draft["draft_hash"], "typed_repo_confirmation": SANDBOX})
    assert response.status_code == 409 and "disabled" in response.json()["detail"]["message"]
    assert h.posts() == []


def test_cancel_is_audited(indexed: Settings) -> None:
    h = Harness(indexed, script=[SEARCH, "It compares hashes [S1]."])
    draft = prepare(h, indexed.project_root)
    assert h.post(f"/api/drafts/{draft['id']}/cancel").json() == {"cancelled": True}
    records = [json.loads(line) for line in indexed.audit_log_path.read_text().splitlines()]
    assert records[-1]["action"] == "cancelled" and "body" not in records[-1]
    assert h.posts() == []


def test_api_has_no_route_the_llm_could_use_to_approve(indexed: Settings) -> None:
    h = Harness(indexed)
    session = h.post("/api/sessions", json={"path": str(indexed.project_root)}).json()
    assert not any(word in t for t in session["tools"] for word in ("create", "approve", "submit"))


# ---- demo mode ------------------------------------------------------------------------------------------


def test_demo_mode_runs_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = demo_runtime(REPO_ROOT)
    llm = runtime.llm
    assert isinstance(llm, DemoLLM) and runtime.settings.openai_api_key is None
    llm.step_delay = llm.answer_delay = 0
    runtime.index_event_delay = 0
    security = LocalSecurity(port=8765)
    client = TestClient(create_app(runtime, security), base_url=BASE)
    headers = {"X-Session-Token": security.token}
    demo = str(REPO_ROOT / "examples" / "fastapi-auth-demo")
    with client.stream("POST", "/api/index", json={"path": demo}, headers=headers) as r:
        assert sse_events(r)[-1][0] == "done"
    session = client.post("/api/sessions", json={"path": demo, "repo": "fastapi/fastapi"}, headers=headers).json()
    with client.stream("POST", "/api/investigate", json={"session_id": session["id"],
                       "question": "Why does /users/me return 401 even with a bearer token?"}, headers=headers) as r:
        answer = dict(sse_events(r))["answer"]
    assert [c["kind"] for c in answer["causes"]] == ["evidence", "evidence", "hypothesis"]
    assert answer["invalid_citations"] == [] and answer["github_sources"]
    assert {"app/auth.py", "app/config.py"} <= {s["file_path"] for s in answer["project_sources"] if s["cited"]}
