"""Phase 7: write client, drafts, secret scanning, approvals and audit log (no network)."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from app.actions.approval import ApprovalError, IssueApprovalService
from app.actions.audit import AuditLog
from app.actions.issue_draft import BODY_MAX, IssueDraft, build_draft, manual_draft, render_body, sanitize_text, scan_secrets
from app.cli import approval_prompt
from app.config import Settings
from app.integrations.github_client import GitHubClient, RepoRef
from app.integrations.github_errors import GitHubNotConfiguredError, RepoNotAllowedError
from app.integrations.github_write_client import GitHubWriteClient, resolve_write_repo, write_configured
from tests.helpers import GH_PAT, GHP, READ_TOKEN, SANDBOX, SK_OR, WRITE_TOKEN, GitHubMock, issues_endpoint, write_settings

ISSUES = f"/repos/{SANDBOX}/issues"


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def listed(number: int, title: str, created: str | None = None) -> dict:
    return {"number": number, "title": title, "state": "open", "html_url": f"https://github.com/{SANDBOX}/issues/{number}",
            "user": {"login": "someone"}, "labels": [], "comments": 0, "created_at": created or now_iso(),
            "updated_at": created or now_iso(), "body": ""}


def write_client(settings: Settings, route) -> tuple[GitHubWriteClient, GitHubMock]:  # type: ignore[no-untyped-def]
    mock = GitHubMock({ISSUES: route})
    client = GitHubWriteClient(settings, transport=mock.transport,
                               read_client_factory=lambda: GitHubClient(settings, transport=mock.transport))
    return client, mock


def service_for(settings: Settings, route, clock=None):  # type: ignore[no-untyped-def]
    mock = GitHubMock({ISSUES: route})
    kwargs = {"clock": clock} if clock else {}
    service = IssueApprovalService(
        settings, channel="test",
        write_client_factory=lambda: GitHubWriteClient(settings, transport=mock.transport,
                                                       read_client_factory=lambda: GitHubClient(settings, transport=mock.transport)),
        read_client_factory=lambda: GitHubClient(settings, transport=mock.transport), **kwargs)
    return service, mock


def posts(mock: GitHubMock) -> list[httpx.Request]:
    return [r for r in mock.requests if r.method == "POST"]


GOOD_BODY = "## Summary\n\nThe bearer scheme check rejects `Bearer` tokens.\n"


# ---- configuration and allowlists -------------------------------------------------------------


def test_write_disabled_by_default(tmp_path: Path) -> None:
    defaults = Settings(_env_file=None)
    assert defaults.github_write_enabled is False and defaults.mcp_enable_write_tools is False
    assert not write_configured(defaults)
    with pytest.raises(GitHubNotConfiguredError, match="disabled"):
        resolve_write_repo(defaults, SANDBOX)


def test_read_allowlist_does_not_grant_write(tmp_path: Path) -> None:
    settings = write_settings(tmp_path)
    assert resolve_write_repo(settings, SANDBOX).full_name == SANDBOX
    with pytest.raises(RepoNotAllowedError, match="GITHUB_WRITE_ALLOWED_REPOS"):
        resolve_write_repo(settings, "octo-org/demo")  # readable, not writable
    draft = manual_draft(settings, "octo-org/demo", "t", GOOD_BODY)
    assert not draft.valid and "GITHUB_WRITE_ALLOWED_REPOS" in draft.errors[0]


def test_write_needs_token_and_list(tmp_path: Path) -> None:
    assert not write_configured(write_settings(tmp_path, github_write_token=None))
    assert not write_configured(write_settings(tmp_path, github_write_allowed_repos=""))
    with pytest.raises(GitHubNotConfiguredError):
        GitHubWriteClient(write_settings(tmp_path, github_write_token=None))


def test_tokens_are_never_mixed(tmp_path: Path) -> None:
    settings = write_settings(tmp_path)
    # Write: POST uses the write token. Unknown outcome: the follow-up GET uses the read token.
    client, mock = write_client(settings, issues_endpoint(post_error=httpx.ReadTimeout("slow")))
    client.create_issue(SANDBOX, "t", GOOD_BODY)
    post, get = mock.requests
    assert post.method == "POST" and post.headers["authorization"] == f"Bearer {WRITE_TOKEN}"
    assert get.method == "GET" and get.headers["authorization"] == f"Bearer {READ_TOKEN}"
    # Reads never use the write token.
    reader_mock = GitHubMock({ISSUES: issues_endpoint()})
    GitHubClient(settings, transport=reader_mock.transport).list_issues(RepoRef.parse(SANDBOX))
    assert reader_mock.requests[0].headers["authorization"] == f"Bearer {READ_TOKEN}"


# ---- write client ---------------------------------------------------------------------------------


def test_write_client_refuses_everything_but_create_issue(tmp_path: Path) -> None:
    client, mock = write_client(write_settings(tmp_path), issues_endpoint())
    attempts = [
        lambda: client._http.get(ISSUES),
        lambda: client._http.patch(f"{ISSUES}/7", json={"state": "closed"}),
        lambda: client._http.delete(f"{ISSUES}/7"),
        lambda: client._http.post(f"{ISSUES}/7/comments", json={"body": "x"}),
        lambda: client._http.post(f"/repos/{SANDBOX}/labels", json={"name": "x"}),
        lambda: client._http.post(f"https://evil.example.com{ISSUES}", json={}),
    ]
    for attempt in attempts:
        with pytest.raises(RuntimeError, match="may only POST"):
            attempt()
    assert mock.requests == []
    public = {n for n in dir(GitHubWriteClient) if not n.startswith("_")}
    assert public == {"create_issue", "close"}


def test_201_is_parsed(tmp_path: Path) -> None:
    client, mock = write_client(write_settings(tmp_path), issues_endpoint())
    result = client.create_issue(SANDBOX, "Bearer scheme rejected", GOOD_BODY, labels=["bug"])
    assert (result.status, result.number, result.html_url) == ("created", 7, f"https://github.com/{SANDBOX}/issues/7")
    sent = json.loads(posts(mock)[0].content)
    assert sent == {"title": "Bearer scheme rejected", "body": GOOD_BODY, "labels": ["bug"]}


def test_201_without_number_is_not_success(tmp_path: Path) -> None:
    client, _ = write_client(write_settings(tmp_path), issues_endpoint(post_body={"oops": True}))
    assert client.create_issue(SANDBOX, "t", GOOD_BODY).status == "unknown"


def test_timeout_is_unknown_never_retried_and_title_checked(tmp_path: Path) -> None:
    title = "Bearer scheme rejected"
    route = issues_endpoint(post_error=httpx.ReadTimeout("slow"),
                            listing=[listed(9, title), listed(8, "Something else"),
                                     listed(3, title, created="2020-01-01T00:00:00Z")])
    client, mock = write_client(write_settings(tmp_path), route)
    result = client.create_issue(SANDBOX, title, GOOD_BODY)
    assert result.status == "unknown" and result.error_category == "timeout"
    assert len(posts(mock)) == 1  # never retried
    assert [m.number for m in result.possible_matches] == [9]  # exact title, created recently
    assert "probably created" in result.message


@pytest.mark.parametrize("error", [httpx.ConnectError("down"), httpx.RemoteProtocolError("reset")])
def test_connection_errors_are_unknown(tmp_path: Path, error: Exception) -> None:
    client, mock = write_client(write_settings(tmp_path), issues_endpoint(post_error=error))
    result = client.create_issue(SANDBOX, "t", GOOD_BODY)
    assert result.status == "unknown" and len(posts(mock)) == 1
    assert "No recent issue with this title" in result.message


def test_5xx_is_unknown_and_not_retried(tmp_path: Path) -> None:
    client, mock = write_client(write_settings(tmp_path), issues_endpoint(post_status=502, post_body={}))
    result = client.create_issue(SANDBOX, "t", GOOD_BODY)
    assert result.status == "unknown" and result.error_category == "server_error" and len(posts(mock)) == 1


@pytest.mark.parametrize(("status", "headers", "category"), [
    (401, {}, "auth"), (403, {}, "forbidden"), (403, {"x-ratelimit-remaining": "0"}, "rate_limited"),
    (404, {}, "not_found"), (410, {}, "issues_disabled"), (422, {}, "validation"), (429, {}, "rate_limited"),
])
def test_error_codes(tmp_path: Path, status: int, headers: dict, category: str) -> None:
    def route(request):  # type: ignore[no-untyped-def]
        return status, {"message": f"nope {WRITE_TOKEN}"}, headers

    client, mock = write_client(write_settings(tmp_path), route)
    result = client.create_issue(SANDBOX, "t", GOOD_BODY)
    assert result.status == "failed" and result.error_category == category
    assert WRITE_TOKEN not in result.message and READ_TOKEN not in result.message
    assert len(posts(mock)) == 1


def test_write_client_rejects_non_allowlisted_repo_before_sending(tmp_path: Path) -> None:
    client, mock = write_client(write_settings(tmp_path), issues_endpoint())
    with pytest.raises(RepoNotAllowedError):
        client.create_issue("octo-org/demo", "t", GOOD_BODY)
    assert mock.requests == []


# ---- drafts and sanitizing ----------------------------------------------------------------------


SECRETS = {
    "API key (sk-...)": SK_OR + "v1-0123456789abcdefABCDEF0123456789",
    "GitHub token": GHP + "Ab3" * 12,
    "GitHub fine-grained token": GH_PAT + "Zx9_" * 12,
    "AWS access key id": "AKIA" + "ABCDEFGHIJKLMNOP",
    "private key block": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA\n-----END RSA PRIVATE KEY-----",
    "secret-like KEY=value line": "DATABASE_PASSWORD=hunter2hunter2",
    "JSON Web Token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1LTEwMCJ9.c2lnbmF0dXJlLXZhbHVlLWhlcmU",
    "long high-entropy string": "token 9fK2pQ7vL3mN8xR4tY6wZ1aB5cD0eF2gH7jK9",
}


@pytest.mark.parametrize("kind", list(SECRETS))
def test_secret_scan_blocks_and_never_echoes(tmp_path: Path, kind: str) -> None:
    value = SECRETS[kind]
    draft = manual_draft(write_settings(tmp_path), SANDBOX, "Auth fails", f"Context:\n{value}\n")
    assert not draft.valid
    assert any(kind in e for e in draft.errors)
    assert all(value not in e and value[:12] not in e for e in draft.errors)


def test_placeholders_and_hashes_are_not_secrets() -> None:
    assert scan_secrets("Set JWT_SECRET_KEY=change-me and API_TOKEN=<your-token>; commit "
                        "3f2a9c1e0b7d4a6f8e2c1b0a9d8e7f6a5b4c3d2e") == []


def test_sanitizing(tmp_path: Path) -> None:
    project = tmp_path / "proj"
    project.mkdir()
    text = (f"See {project}/app/auth.py and /home/someone/secret-notes/todo.txt.\n"
            "Ping @octocat; fixes #12 and closes other/repo#3.\n"
            "`@router.get` stays, as does\n```\n@app.get(\"/x\")  # fixes #9\n```\n"
            "<untrusted-github-content source=\"x#1\">\nquoted\n</untrusted-github-content>\n"
            "This issue was generated by an AI assistant.\nCo-Authored-By: Bot <bot@example.com>\n")
    clean, notes = sanitize_text(text, project)
    assert str(project) not in clean and "app/auth.py" in clean
    assert "/home/someone" not in clean and "<local-path>/todo.txt" in clean
    assert "`@octocat`" in clean and "related to #12" in clean and "related to other/repo#3" in clean
    assert "`@router.get`" in clean and '@app.get("/x")  # fixes #9' in clean  # code untouched
    assert "untrusted-github-content" not in clean and "quoted" in clean
    assert "generated by" not in clean.lower() and "co-authored" not in clean.lower()
    assert len(notes) == 6


def test_body_template_and_no_credit(tmp_path: Path) -> None:
    from app.actions.issue_draft import EvidenceRef

    related = [EvidenceRef(cite_id="G1", kind="github", label="fastapi/fastapi#10177: HTTPBearer returns 403",
                           url="https://github.com/fastapi/fastapi/issues/10177", number=10177)]
    body = render_body("Summary text.", ["Send `Authorization: Bearer x`"], "200 OK", "401",
                       "401 Invalid authentication scheme", ["app/auth.py"], related, ["Compare case-insensitively"])
    for heading in ("Summary", "Steps to reproduce", "Expected behavior", "Actual behavior", "Error messages",
                    "Relevant files", "Related issues/PRs (leads, not confirmed)", "Proposed investigation steps"):
        assert f"## {heading}" in body
    assert "[#10177](https://github.com/fastapi/fastapi/issues/10177)" in body and "> fastapi/fastapi#10177" in body
    for credit in ("generated", "assistant", "claude", "cursor", "co-authored", " ai "):
        assert credit not in body.lower()


def test_title_body_label_limits(tmp_path: Path) -> None:
    settings = write_settings(tmp_path)
    assert manual_draft(settings, SANDBOX, "line one\nline two", GOOD_BODY).title == "line one line two"
    assert any("256" in e for e in manual_draft(settings, SANDBOX, "x" * 257, GOOD_BODY).errors)
    assert any("limit" in e for e in manual_draft(settings, SANDBOX, "t", "y" * (BODY_MAX + 1)).errors)
    assert any("Labels" in e for e in manual_draft(settings, SANDBOX, "t", GOOD_BODY, labels=["a,b"]).errors)
    assert manual_draft(settings, SANDBOX, "t", GOOD_BODY, labels=["bug", "auth"]).valid


def test_hash_binds_content(tmp_path: Path) -> None:
    settings = write_settings(tmp_path)
    a = manual_draft(settings, SANDBOX, "t", GOOD_BODY)
    b = manual_draft(settings, SANDBOX, "t", GOOD_BODY + "x")
    assert a.draft_hash != b.draft_hash and len(a.draft_hash) == 64


# ---- approval flow --------------------------------------------------------------------------------


def make_service(tmp_path: Path, route=None, clock=None, **settings_overrides):  # type: ignore[no-untyped-def]
    settings = write_settings(tmp_path, **settings_overrides)
    service, mock = service_for(settings, route or issues_endpoint(), clock)
    draft = service.add_draft(manual_draft(settings, SANDBOX, "Bearer scheme rejected", GOOD_BODY))
    return service, mock, draft


def test_submit_requires_an_approval(tmp_path: Path) -> None:
    service, mock, _ = make_service(tmp_path)
    with pytest.raises(ApprovalError, match="Approve the draft first"):
        service.submit("not-an-approval")
    assert mock.requests == []


def test_wrong_confirmation_cancels(tmp_path: Path) -> None:
    service, mock, draft = make_service(tmp_path)
    for typed in ("yes", "y", "approve", SANDBOX.upper(), "octo-org/sandbox2", ""):
        with pytest.raises(ApprovalError, match="Cancelled"):
            service.approve(draft.draft_id, typed)
    assert mock.requests == [] and service.approvals == {}
    assert {r.action for r in service.audit.read()} == {"cancelled"}


def test_approved_submit_creates_once(tmp_path: Path) -> None:
    service, mock, draft = make_service(tmp_path)
    approval = service.approve(draft.draft_id, SANDBOX)
    result = service.submit(approval.approval_id)
    assert result.status == "created" and result.number == 7
    assert len(posts(mock)) == 1
    with pytest.raises(ApprovalError, match="already used"):
        service.submit(approval.approval_id)  # single use
    with pytest.raises(ApprovalError, match="already submitted"):
        service.approve(draft.draft_id, SANDBOX)  # no second attempt for the same draft
    assert len(posts(mock)) == 1


def test_approval_expires(tmp_path: Path) -> None:
    now = [1000.0]
    service, mock, draft = make_service(tmp_path, clock=lambda: now[0], approval_ttl_seconds=60)
    approval = service.approve(draft.draft_id, SANDBOX)
    now[0] += 61
    with pytest.raises(ApprovalError, match="expired"):
        service.submit(approval.approval_id)
    assert posts(mock) == []


def test_edit_after_approval_requires_new_approval(tmp_path: Path) -> None:
    service, mock, draft = make_service(tmp_path)
    approval = service.approve(draft.draft_id, SANDBOX)
    old_hash = draft.draft_hash
    edited = service.edit_draft(draft.draft_id, body=GOOD_BODY + "\nMore detail.\n")
    assert edited.draft_hash != old_hash
    with pytest.raises(ApprovalError, match="already used or voided"):
        service.submit(approval.approval_id)
    assert posts(mock) == []
    fresh = service.approve(draft.draft_id, SANDBOX)
    assert fresh.draft_hash == edited.draft_hash
    assert service.submit(fresh.approval_id).status == "created"


def test_tampering_with_draft_object_is_detected(tmp_path: Path) -> None:
    service, mock, draft = make_service(tmp_path)
    approval = service.approve(draft.draft_id, SANDBOX)
    draft.body += "\nsneaky change"  # bypassing edit_draft
    with pytest.raises(ApprovalError, match="changed after it was approved"):
        service.submit(approval.approval_id)
    assert posts(mock) == []


def test_double_submit_is_blocked(tmp_path: Path) -> None:
    service, mock, draft = make_service(tmp_path)
    first = service.approve(draft.draft_id, SANDBOX)
    second = service.approve(draft.draft_id, SANDBOX)  # e.g. a double click before submit
    assert service.submit(first.approval_id).status == "created"
    with pytest.raises(ApprovalError, match="already had its creation attempt"):
        service.submit(second.approval_id)
    assert len(posts(mock)) == 1


def test_similar_issue_needs_second_confirmation(tmp_path: Path) -> None:
    route = issues_endpoint(listing=[listed(4, "Bearer scheme is rejected")])
    service, mock, draft = make_service(tmp_path, route=route)
    approval = service.approve(draft.draft_id, SANDBOX)
    first = service.submit(approval.approval_id)
    assert first.status == "needs_confirmation" and first.similar_issues[0].number == 4
    assert posts(mock) == []
    second = service.submit(approval.approval_id, confirm_similar=True)
    assert second.status == "created" and len(posts(mock)) == 1


def test_disabled_write_or_invalid_draft_cannot_be_approved(tmp_path: Path) -> None:
    service, mock, draft = make_service(tmp_path, github_write_enabled=False)
    with pytest.raises(ApprovalError):
        service.approve(draft.draft_id, SANDBOX)
    service2, mock2, _ = make_service(tmp_path)
    bad = service2.add_draft(manual_draft(service2.settings, SANDBOX, "t", "key " + SK_OR + "v1-0123456789abcdefABCDEF0123"))
    with pytest.raises(ApprovalError, match="validation errors"):
        service2.approve(bad.draft_id, SANDBOX)
    assert mock.requests == [] and posts(mock2) == []


def test_unknown_outcome_is_reported(tmp_path: Path) -> None:
    route = issues_endpoint(post_error=httpx.ReadTimeout("slow"), listing=[listed(11, "Bearer scheme rejected")])
    service, mock, draft = make_service(tmp_path, route=route)
    result = service.submit(service.approve(draft.draft_id, SANDBOX).approval_id, confirm_similar=True)
    assert result.status == "unknown" and result.number is None and result.possible_matches[0].number == 11
    assert len(posts(mock)) == 1
    with pytest.raises(ApprovalError):
        service.submit(service.approve(draft.draft_id, SANDBOX).approval_id)  # no blind retry


# ---- audit log ------------------------------------------------------------------------------------


def test_audit_records_have_no_body_title_or_token(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG):
        service, _, draft = make_service(tmp_path)
        service.cancel(draft.draft_id)
        approval = service.approve(draft.draft_id, SANDBOX)
        service.submit(approval.approval_id)
    raw = service.settings.audit_log_path.read_text()
    records = [json.loads(line) for line in raw.splitlines()]
    assert [r["action"] for r in records] == ["cancelled", "approved", "created"]
    created = records[-1]
    assert created["issue_number"] == 7 and created["repo"] == SANDBOX and created["draft_hash"] == draft.draft_hash
    assert created["title_length"] == len(draft.title)
    for forbidden in (GOOD_BODY.strip(), "bearer scheme check", draft.title, WRITE_TOKEN, READ_TOKEN):
        assert forbidden not in raw and forbidden not in caplog.text


# ---- CLI approval prompt -------------------------------------------------------------------------


def test_cli_prompt_cancel_and_approve(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    service, mock, draft = make_service(tmp_path)
    assert approval_prompt(service, draft, ask=lambda _: "yes") is None
    out = capsys.readouterr().out
    assert f"Destination repository: {SANDBOX}" in out and "Nothing was created" in out
    assert posts(mock) == []

    service2, mock2, draft2 = make_service(tmp_path)
    result = approval_prompt(service2, draft2, ask=lambda _: SANDBOX)
    assert result.status == "created" and "Created issue #7" in capsys.readouterr().out
    assert len(posts(mock2)) == 1


def test_cli_prompt_similar_requires_create_anyway(tmp_path: Path) -> None:
    route = issues_endpoint(listing=[listed(4, "Bearer scheme rejected!")])
    service, mock, draft = make_service(tmp_path, route=route)
    answers = iter([SANDBOX, "y"])
    assert approval_prompt(service, draft, ask=lambda _: next(answers)) is None
    assert posts(mock) == []
    service2, mock2, draft2 = make_service(tmp_path, route=route)
    answers2 = iter([SANDBOX, "create anyway"])
    assert approval_prompt(service2, draft2, ask=lambda _: next(answers2)).status == "created"


def test_build_draft_from_findings(tmp_path: Path) -> None:
    from app.actions.issue_draft import EvidenceRef

    project = tmp_path / "proj"
    project.mkdir()
    draft = build_draft(write_settings(tmp_path), SANDBOX, "401 with Bearer tokens",
                        f"The parser in {project}/app/auth.py compares the scheme case-sensitively. cc @maintainer",
                        relevant_files=["app/auth.py"],
                        evidence=[EvidenceRef(cite_id="S1", kind="project", label="app/auth.py", file_path="app/auth.py")],
                        project_root=project)
    assert draft.valid and isinstance(draft, IssueDraft)
    assert str(project) not in draft.body and "`@maintainer`" in draft.body and "`app/auth.py`" in draft.body


def test_citation_ids_become_github_references(tmp_path: Path) -> None:
    from app.actions.issue_draft import EvidenceRef, resolve_citations

    refs = [EvidenceRef(cite_id="S3", kind="project", label="app/auth.py (chars 0-833)", file_path="app/auth.py"),
            EvidenceRef(cite_id="G1", kind="github", label="fastapi/fastapi#10177: HTTPBearer", number=10177,
                        url="https://github.com/fastapi/fastapi/issues/10177")]
    text = "The check is case-sensitive [S3]. Upstream too [G1][S3]. Grouped [S3, G1]. Unknown [S9]."
    assert resolve_citations(text, refs) == ("The check is case-sensitive (`app/auth.py`). Upstream too "
                                             "(fastapi/fastapi#10177, `app/auth.py`). Grouped (`app/auth.py`, "
                                             "fastapi/fastapi#10177). Unknown.")
    # Regression: text that isn't a citation keeps its spacing ("in .env.example" stayed "in.env.example").
    untouched = "Set it in .env.example , then run ./start.sh ; see README ."
    assert resolve_citations(untouched, refs) == untouched
    assert resolve_citations("in .env.example [S3][S3] and README [S3]", refs) == \
        "in .env.example (`app/auth.py`) and README (`app/auth.py`)"
    draft = build_draft(write_settings(tmp_path), SANDBOX, "Auth [S3]", "Bug in parser [S3].", evidence=refs)
    assert "[S3]" not in draft.body and "(`app/auth.py`)" in draft.body and draft.title == "Auth (`app/auth.py`)"
