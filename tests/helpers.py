"""Small helpers shared by tests."""

from pathlib import Path

import pymupdf

from app.config import Settings

# Files in tests/fixtures/sample_project that ingestion should accept.
FIXTURE_FILES = {
    "README.md", "main.py", "auth.py", "database.py", "config.yaml", "data.json",
    "docs/troubleshooting.md", ".env.example",
}


def make_settings(root: Path, **overrides: object) -> Settings:
    """Settings for tests: ignore any real .env file in the working directory."""
    overrides.setdefault("data_dir", root.parent / "web-data")  # conversation history stays in tmp
    return Settings(_env_file=None, project_root=root, **overrides)  # type: ignore[call-arg]


def make_pdf(path: Path, pages: list[str]) -> None:
    """Write a small PDF with one line of text per page."""
    with pymupdf.open() as pdf:
        for text in pages:
            pdf.new_page().insert_text((72, 72), text)
        pdf.save(path)


# ---- GitHub mocks ----------------------------------------------------------------

GITHUB_FIXTURES = Path(__file__).parent / "fixtures" / "github"


def github_fixture(name: str) -> object:
    import json

    return json.loads((GITHUB_FIXTURES / name).read_text())


class GitHubMock:
    """An httpx.MockTransport that serves fixture responses by path and records requests.

    ``routes`` maps a URL path to a response spec, a list of specs served in
    order (the last one repeats), or a function ``request -> spec``. A spec is
    ``(status, json_body, headers)``.
    """

    RATE_HEADERS = {
        "x-ratelimit-limit": "60", "x-ratelimit-remaining": "57", "x-ratelimit-used": "3",
        "x-ratelimit-reset": "1791000000", "x-ratelimit-resource": "core",
    }

    def __init__(self, routes: dict[str, object]) -> None:
        import httpx

        self.routes = routes
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request):  # type: ignore[no-untyped-def]
        import httpx

        self.requests.append(request)
        spec = self.routes.get(request.url.path)
        if spec is None:
            return httpx.Response(404, json={"message": "Not Found"}, headers=self.RATE_HEADERS)
        if callable(spec):  # a function of the request, e.g. to serve pages by ?page=
            spec = spec(request)
        if isinstance(spec, list):
            index = min(sum(1 for r in self.requests if r.url.path == request.url.path), len(spec)) - 1
            spec = spec[index]
        if isinstance(spec, Exception):
            raise spec
        status, body, headers = spec
        return httpx.Response(status, json=body, headers={**self.RATE_HEADERS, **(headers or {})})


# ---- GitHub write mocks (Phase 7) ------------------------------------------------------

# Fake token prefixes are assembled from parts so the literal prefixes never appear in the repo
# (a plain grep for real-looking credentials stays clean).
GHP = "gh" + "p_"
GH_PAT = "github" + "_pat_"
SK_OR = "sk-" + "or-"
READ_TOKEN = GHP + "READtokenFORtestsONLY0123456789abcdef"
WRITE_TOKEN = GH_PAT + "WRITEtokenFORtestsONLY_0123456789abcdefABCDEF0123456789"
SANDBOX = "octo-org/sandbox"


def write_settings(root: Path, **overrides: object) -> Settings:
    """Settings with writing enabled for the sandbox repo only (tests)."""
    from pydantic import SecretStr

    defaults: dict[str, object] = {
        "github_token": SecretStr(READ_TOKEN),
        "github_write_token": SecretStr(WRITE_TOKEN),
        "github_write_enabled": True,
        "github_write_allowed_repos": SANDBOX,
        "github_default_repo": "octo-org/demo",
        "github_allowed_repos": f"octo-org/demo,{SANDBOX}",
        "audit_log_path": root / "audit" / "github_writes.jsonl",
    }
    return make_settings(root, **{**defaults, **overrides})


def issues_endpoint(created: dict | None = None, listing: list | None = None, post_status: int = 201,
                    post_body: dict | None = None, post_error: Exception | None = None):  # type: ignore[no-untyped-def]
    """A route for /repos/{sandbox}/issues: POST creates, GET lists."""

    def serve(request):  # type: ignore[no-untyped-def]
        if request.method == "POST":
            if post_error is not None:
                raise post_error
            body = post_body if post_body is not None else (created or {
                "number": 7, "html_url": f"https://github.com/{SANDBOX}/issues/7"})
            return post_status, body, {}
        return 200, listing or [], {}

    return serve
