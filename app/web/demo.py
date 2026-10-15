"""--demo mode: the full app with no network, no keys and no cost.

- Settings are built explicitly (``.env`` is ignored), with fake embeddings and
  a temporary Chroma directory.
- The project is ``examples/fastapi-auth-demo``.
- GitHub reads come from an in-memory mock with a few realistic fastapi/fastapi
  issues; "writes" go to a mock ``demo-org/sandbox`` (nothing real is ever created).
- The LLM is scripted: it chooses tools like a real model would (search, read
  the files it found, check GitHub) with short delays, then writes its answer
  from the tool results it actually received, so every citation is valid.
"""

import json
import re
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from pydantic import SecretStr

from app.actions.approval import IssueApprovalService
from app.config import Settings
from app.integrations.github_client import GitHubClient
from app.integrations.github_write_client import GitHubWriteClient
from app.llm.base import ChatMessage, ChatResponse, LLMResponse, ToolCall, ToolChoice, ToolSpec
from app.mcp_server.tools.github_tools import GitHubTools
from app.orchestration.tool_backends import InProcessBackend
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.web.sessions import Runtime

DEMO_REPO = "fastapi/fastapi"
DEMO_WRITE_REPO = "demo-org/sandbox"
STEP_DELAY = 0.9
ANSWER_DELAY = 1.4


def demo_project(repo_root: Path) -> Path:
    return (repo_root / "examples" / "fastapi-auth-demo").resolve()


def demo_settings(repo_root: Path, workdir: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        project_root=demo_project(repo_root),
        embedding_provider="fake",
        chroma_persist_dir=workdir / "chroma",
        llm_provider="fake",
        llm_model="demo-scripted-llm",
        anthropic_api_key=None,
        openai_api_key=None,
        mcp_allowed_project_roots=str((repo_root / "examples").resolve()),
        github_token=None,
        github_default_repo=DEMO_REPO,
        github_allowed_repos=DEMO_REPO,
        github_write_token=SecretStr("demo-only-not-a-real-token"),
        github_write_enabled=True,
        github_write_allowed_repos=DEMO_WRITE_REPO,
        audit_log_path=workdir / "audit" / "github_writes.jsonl",
        data_dir=workdir / "data",
    )


# ---- mock GitHub ------------------------------------------------------------------------

_RATE = {"x-ratelimit-limit": "60", "x-ratelimit-remaining": "58", "x-ratelimit-used": "2",
         "x-ratelimit-reset": str(int((datetime.now(UTC) + timedelta(hours=1)).timestamp())),
         "x-ratelimit-resource": "core"}


def _issue(number: int, title: str, state: str, labels: list[str], body: str, comments: int,
           created: str, updated: str, is_pr: bool = False) -> dict[str, Any]:
    kind = "pull" if is_pr else "issues"
    item = {"number": number, "title": title, "state": state, "labels": [{"name": n} for n in labels],
            "user": {"login": "fastapi-user"}, "comments": comments, "created_at": created, "updated_at": updated,
            "closed_at": updated if state == "closed" else None,
            "state_reason": "completed" if state == "closed" else None,
            "html_url": f"https://github.com/{DEMO_REPO}/{kind}/{number}",
            "repository_url": f"https://api.github.com/repos/{DEMO_REPO}", "body": body}
    if is_pr:
        item["pull_request"] = {"merged_at": updated, "html_url": item["html_url"]}
    return item


DEMO_ISSUES = [
    _issue(10177, "HTTPBearer security scheme is returning 403 instead or 401", "closed", ["bug"],
           "HTTPBearer returns 403 when the Authorization header is missing or malformed; 401 is expected.",
           12, "2023-08-31T10:00:00Z", "2025-11-28T09:00:00Z"),
    _issue(13786, "Use 401 status code in security classes when credentials are missing", "closed", ["bug"],
           "Changes the default for missing credentials from 403 to 401 in HTTPBearer and related classes.",
           9, "2025-06-30T10:00:00Z", "2025-11-24T12:00:00Z", is_pr=True),
    _issue(2026, "HTTPBearer security scheme is returning 403 instead or 401", "closed", ["question"],
           "When the bearer token is missing the dependency raises 403 Forbidden rather than 401.",
           11, "2020-09-08T10:00:00Z", "2023-02-28T10:00:00Z"),
    _issue(5601, "No Authorization header received behind a reverse proxy", "closed", ["question"],
           "After login the Authorization header is missing in requests that go through nginx.",
           7, "2022-11-03T10:00:00Z", "2023-02-28T10:00:00Z"),
]


def demo_github_transport() -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/search/issues":
            words = [w for w in re.findall(r"[a-z0-9]+", request.url.params.get("q", "").lower())
                     if ":" not in w and w not in {"repo", "fastapi", "is", "issue", "pr", "state", "open", "closed"}]
            items = [i for i in DEMO_ISSUES
                     if not words or any(w in (i["title"] + " " + i["body"]).lower() for w in words)]
            return httpx.Response(200, headers={**_RATE, "x-ratelimit-resource": "search"},
                                  json={"total_count": len(items), "incomplete_results": False, "items": items})
        if path == f"/repos/{DEMO_REPO}":
            return httpx.Response(200, headers=_RATE, json={
                "full_name": DEMO_REPO, "description": "FastAPI framework, high performance, easy to learn",
                "default_branch": "master", "stargazers_count": 103056, "forks_count": 10029,
                "open_issues_count": 88, "language": "Python", "topics": ["python", "api"],
                "license": {"spdx_id": "MIT"}, "html_url": f"https://github.com/{DEMO_REPO}",
                "visibility": "public", "pushed_at": "2026-10-08T10:00:00Z"})
        match = re.fullmatch(rf"/repos/{DEMO_REPO}/(?:issues|pulls)/(\d+)(/comments|/files)?", path)
        if match:
            item = next((i for i in DEMO_ISSUES if i["number"] == int(match.group(1))), None)
            if item is None:
                return httpx.Response(404, headers=_RATE, json={"message": "Not Found"})
            if match.group(2) == "/comments":
                return httpx.Response(200, headers=_RATE, json=[{
                    "user": {"login": "maintainer"}, "author_association": "MEMBER", "created_at": item["updated_at"],
                    "html_url": item["html_url"] + "#issuecomment-1",
                    "body": "This was handled in #13786, available in FastAPI 0.122.0."}])
            if match.group(2) == "/files":
                return httpx.Response(200, headers=_RATE, json=[
                    {"filename": "fastapi/security/http.py", "status": "modified", "additions": 24, "deletions": 9}])
            data = dict(item)
            data.update({"merged": True, "base": {"ref": "master"}, "head": {"ref": "security-401"},
                         "commits": 3, "additions": 120, "deletions": 40, "changed_files": 6})
            return httpx.Response(200, headers=_RATE, json=data)
        if path == f"/repos/{DEMO_REPO}/issues":
            return httpx.Response(200, headers=_RATE, json=DEMO_ISSUES)
        if path == f"/repos/{DEMO_REPO}/pulls":
            state = request.url.params.get("state", "open")
            pulls = [{**i, "draft": False, "merged_at": i["updated_at"], "base": {"ref": "master"},
                      "head": {"ref": "security-401"}} for i in DEMO_ISSUES
                     if "pull_request" in i and state in ("all", i["state"])]
            return httpx.Response(200, headers=_RATE, json=pulls)
        if path == f"/repos/{DEMO_WRITE_REPO}/issues":
            if request.method == "POST":  # a mock; nothing leaves this process
                return httpx.Response(201, json={"number": 42, "html_url": f"https://github.com/{DEMO_WRITE_REPO}/issues/42"})
            return httpx.Response(200, headers=_RATE, json=[])
        return httpx.Response(404, headers=_RATE, json={"message": "Not Found"})

    return httpx.MockTransport(handle)


# ---- scripted LLM ---------------------------------------------------------------------------------

_STOPWORDS = {"why", "does", "do", "my", "the", "a", "an", "is", "are", "to", "even", "though", "i", "when", "what",
              "how", "which", "in", "of", "and", "or", "it", "for", "with", "this", "that", "send", "provide"}


def _question(messages: list[ChatMessage]) -> str:
    text = messages[0].content if messages else ""
    return text.split("Question:", 1)[-1].strip()


def _is_auth_question(question: str) -> bool:
    return bool(re.search(r"(?i)401|bearer|token|auth|unauthori[sz]ed|jwt|login", question))


class DemoLLM:
    """A scripted, tool-calling stand-in for a real model (demo mode only)."""

    provider = "demo"
    model = "demo-scripted-llm"

    def __init__(self, step_delay: float = STEP_DELAY, answer_delay: float = ANSWER_DELAY) -> None:
        self.step_delay = step_delay
        self.answer_delay = answer_delay
        self._calls = 0

    # -- quick answer (one call, no tools)
    def generate(self, system: str, user: str) -> LLMResponse:
        time.sleep(self.answer_delay)
        sources = re.findall(r'<source id="(S\d+)" file="([^"]+)"', user)
        lines = ["## From the project"]
        for cid, file in sources[:3]:
            lines.append(f"- `{file}` is relevant to this question [{cid}].")
        if not sources:
            lines = ["INSUFFICIENT EVIDENCE", "", "No relevant sources were found."]
        text = "\n".join(lines)
        return LLMResponse(text=text, model=self.model, provider=self.provider, input_tokens=len(user) // 4,
                           output_tokens=len(text) // 4, latency_ms=self.answer_delay * 1000, stop_reason="end_turn")

    # -- investigations
    def chat(self, system: str, messages: list[ChatMessage], tools: list[ToolSpec],
             tool_choice: ToolChoice = "auto") -> ChatResponse:
        question = _question(messages)
        names = {t.name for t in tools}
        rounds = sum(1 for m in messages if m.role == "assistant" and m.tool_calls)
        results = [json.loads(m.content) for m in messages if m.role == "tool"]
        calls = [] if tool_choice == "none" else self._next_calls(question, rounds, names, results)
        if calls:
            time.sleep(self.step_delay)
            return self._response("", calls, messages)
        time.sleep(self.answer_delay)
        return self._response(self._answer(question, results), [], messages)

    def _next_calls(self, question: str, rounds: int, names: set[str], results: list[dict]) -> list[tuple[str, dict]]:
        auth = _is_auth_question(question)
        if rounds == 0 and "search_project" in names:
            terms = [w for w in re.findall(r"[\w/.-]+", question.lower()) if w not in _STOPWORDS]
            return [("search_project", {"query": " ".join(terms[:8]) or question[:80], "top_k": 5})]
        if rounds == 1 and auth and "read_project_file" in names:
            return [("read_project_file", {"file_path": "app/auth.py"}),
                    ("read_project_file", {"file_path": "app/config.py"})]
        if rounds == 2 and auth and "search_github_issues" in names:
            return [("search_github_issues", {"query": "HTTPBearer 401 bearer token", "kind": "any", "limit": 5})]
        if rounds == 0 and "search_github_issues" in names:  # GitHub-only mode
            return [("search_github_issues", {"query": question[:120], "kind": "any", "limit": 5})]
        return []

    def _response(self, text: str, calls: list[tuple[str, dict]], messages: list[ChatMessage]) -> ChatResponse:
        tool_calls = []
        for name, args in calls:
            self._calls += 1
            tool_calls.append(ToolCall(id=f"demo_{self._calls}", name=name, arguments=args))
        prompt_chars = sum(len(m.content) for m in messages)
        return ChatResponse(text=text, tool_calls=tool_calls, model=self.model, provider=self.provider,
                            input_tokens=1200 + prompt_chars // 4, output_tokens=60 * len(calls) + len(text) // 4,
                            latency_ms=(self.step_delay if calls else self.answer_delay) * 1000,
                            stop_reason="tool_use" if calls else "end_turn",
                            message=ChatMessage(role="assistant", content=text, tool_calls=tool_calls))

    @staticmethod
    def _answer(question: str, results: list[dict]) -> str:
        files: dict[str, str] = {}
        issues: list[tuple[str, int, str]] = []
        for r in results:
            res = r.get("result") or {}
            for hit in res.get("hits", []):
                files.setdefault(hit["file_path"], hit["cite"])
            if "cite" in res and "file_path" in res:
                files.setdefault(res["file_path"], res["cite"])
            for item in res.get("items", []):
                title = re.sub(r"</?untrusted-[a-z-]+[^>]*>", "", item["title"]).strip()
                issues.append((item["cite"], item["number"], title))

        def cite(path: str) -> str:
            return f"[{files[path]}]" if path in files else ""

        if _is_auth_question(question) and "app/auth.py" in files:
            env = cite(".env.example") or cite("docs/auth.md") or cite("README.md")
            parts = [
                "## Summary",
                "Requests to `/users/me` return 401 because of two independent problems in the auth path: the "
                f"header parser only accepts a lowercase `bearer` scheme {cite('app/auth.py')}, and the signing "
                f"secret is read from a different environment variable than the one the docs describe "
                f"{cite('app/config.py')}{env}.",
                "",
                "## From the project",
                f"- `parse_authorization_header` rejects any scheme that isn't exactly `\"bearer\"`, so the standard "
                f"`Authorization: Bearer <token>` header raises *Invalid authentication scheme* {cite('app/auth.py')}.",
                f"- `load_settings` reads `JWT_SECRET` and silently falls back to `\"dev-secret\"` "
                f"{cite('app/config.py')}, "
                f"while the setup docs ask for `JWT_SECRET_KEY` {env}.",
                f"- `get_current_user` turns every `AuthError` into `401` with `WWW-Authenticate: Bearer` "
                f"{cite('app/auth.py')}.",
            ]
            if issues:
                parts += ["", "## From GitHub"]
                for cid, number, title in issues[:2]:
                    parts.append(f"- [{cid}] {DEMO_REPO}#{number}: {title}. A related lead about 401 vs 403, "
                                 "not proof that it applies here.")
            parts += [
                "", "## General knowledge",
                "HTTP authentication schemes are case-insensitive (RFC 7235), so servers should accept `Bearer` "
                "and `bearer` alike.",
                "", "## Possible causes",
                f"1. **Case-sensitive scheme check** (supported by evidence): `scheme != \"bearer\"` rejects the "
                f"documented `Bearer` prefix {cite('app/auth.py')}.",
                f"2. **Secret read from the wrong variable** (supported by evidence): the code reads `JWT_SECRET`, "
                f"the docs set `JWT_SECRET_KEY`, so signatures fail with *Invalid token* {cite('app/config.py')}{env}.",
                "3. **Token older than `ACCESS_TOKEN_MAX_AGE_MINUTES`** (hypothesis): the age check uses `iat`, so "
                "long-lived tokens can be rejected even before `exp`.",
                "", "## Suggested debugging steps",
                "1. Read the `detail` field of the 401 response: it names the failing check.",
                "2. Retry with a lowercase `bearer` scheme to confirm the parser issue.",
                "3. Print which of `JWT_SECRET` / `JWT_SECRET_KEY` the running process actually has.",
                "", "## Open questions",
                "- The token issuer's code isn't in this project, so its signing secret can't be verified here.",
            ]
            return "\n".join(parts)

        ranked = list(files.items())[:3]
        if not ranked and not issues:
            return ("## Summary\nThe tools returned nothing relevant, so this can't be answered from the project.\n\n"
                    "## Open questions\n- Try indexing the project or rephrasing with names from the code.")
        lines = ["## Summary", "Here is what the most relevant sources show.", "", "## From the project"]
        lines += ([f"- `{path}` is the most relevant match [{cid}]." for path, cid in ranked]
                  or ["- No project matches."])
        if issues:
            lines += ["", "## From GitHub"] + [f"- [{cid}] #{n}: {t} (a lead, not proof)." for cid, n, t in issues[:3]]
        lines += ["", "## Open questions", "- This is a scripted demo answer."]
        return "\n".join(lines)


def demo_runtime(repo_root: Path) -> Runtime:
    workdir = Path(tempfile.mkdtemp(prefix="ai-dev-assistant-demo-"))
    settings = demo_settings(repo_root, workdir)
    llm = DemoLLM()
    github = demo_github_transport()

    def approvals(project: Path) -> IssueApprovalService:
        return IssueApprovalService(
            settings, channel="web-demo", project_root=project,
            write_client_factory=lambda: GitHubWriteClient(
                settings, transport=github, read_client_factory=lambda: GitHubClient(settings, transport=github)),
            read_client_factory=lambda: GitHubClient(settings, transport=github))

    return Runtime(
        settings=settings,
        make_llm=lambda: llm,  # type: ignore[return-value]
        make_backend=lambda project: InProcessBackend(settings, project, FakeEmbeddingProvider(),
                                                      github_transport=github),
        make_approvals=approvals,
        make_embeddings=FakeEmbeddingProvider,
        make_github=lambda: GitHubTools(settings, transport=github),
        demo=True,
        index_event_delay=0.35,
    )
