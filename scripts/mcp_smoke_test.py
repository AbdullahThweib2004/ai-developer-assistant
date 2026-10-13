"""Smoke test: launch the MCP server over stdio and call a few tools.

Usage (from the repository root, after indexing this repository):
    uv run python -m app.cli index --path . --rebuild
    uv run python scripts/mcp_smoke_test.py ["project query"] [--github-repo owner/name] [--github-query "..."]

GitHub calls are read-only and work without a token (the server reads
GITHUB_TOKEN from .env when it's set).

The server runs as a subprocess, exactly as an MCP client (Inspector, Claude
Desktop, Cursor) would start it. Only the variables passed in ``env`` (plus a
minimal allow-list) reach it, so the allowed root and index location are set here.
"""

import argparse
import json
import sys
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

REPO = Path(__file__).resolve().parents[1]


def text_of(result) -> str:  # type: ignore[no-untyped-def]
    return " ".join(getattr(c, "text", "") for c in result.content)


def unwrap(text: str) -> str:
    """Strip the untrusted-content wrapper tags for display."""
    return text.split("\n", 1)[1].rsplit("\n", 1)[0] if text.startswith("<untrusted-") else text


def rate(data: dict) -> str:
    r = data.get("rate_limit") or {}
    auth = "token" if r.get("authenticated") else "no token"
    return f"rate limit ({r.get('resource')}): {r.get('remaining')}/{r.get('limit')} remaining ({auth})"


async def main(query: str, github_repo: str, github_query: str) -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        cwd=str(REPO),
        env={
            "MCP_ALLOWED_PROJECT_ROOTS": str(REPO),
            "CHROMA_PERSIST_DIR": str(REPO / "chroma_db"),
            "GITHUB_ALLOWED_REPOS": github_repo,
            "GITHUB_DEFAULT_REPO": github_repo,
        },
    )
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
        print(f"Tools ({len(tools)}):")
        for tool in tools:
            hint = "read-only" if tool.annotations and tool.annotations.read_only_hint else "?"
            print(f"  - {tool.name} [{hint}]: {(tool.description or '').splitlines()[0]}")

        ping = await client.call_tool("ping", {})
        print(f"\nping -> {json.dumps(ping.structured_content)}")

        result = await client.call_tool(
            "search_project", {"project_path": str(REPO), "query": query, "top_k": 3}
        )
        if result.is_error:
            print("\nsearch_project error:", text_of(result))
            return
        data = result.structured_content
        print(f"\nsearch_project({query!r}) -> {len(data['hits'])} hits")
        for hit in data["hits"]:
            body = hit["content"].split("\n", 1)[1].rsplit("\n", 1)[0]  # strip the wrapper tags for display
            preview = " ".join(body.split())[:140]
            print(f"  {hit['rank']}. [{hit['score']:.3f}] {hit['file_path']} "
                  f"(chunk {hit['chunk_index']}, {hit['location']})\n     {preview}...")
        print(f"\nnote: {data['note']}")

        repo = await client.call_tool("get_github_repository", {"repo": github_repo})
        if repo.is_error:
            print(f"\nget_github_repository error: {text_of(repo)}")
            return
        r = repo.structured_content
        print(f"\nget_github_repository({github_repo}) -> {r['full_name']}: {r['stars']} stars, "
              f"{r['open_issues_count']} open issues+PRs, default branch {r['default_branch']}")
        print(f"  {rate(r)}")

        found = await client.call_tool(
            "search_github_issues", {"repo": github_repo, "query": github_query, "kind": "any", "limit": 5}
        )
        if found.is_error:
            print(f"\nsearch_github_issues error: {text_of(found)}")
            return
        f = found.structured_content
        print(f"\nsearch_github_issues({github_query!r}) -> {f['total_count']} matches, showing {len(f['items'])}")
        print(f"  query sent: {f['query']}")
        for item in f["items"]:
            kind = "PR" if item["is_pull_request"] else "issue"
            print(f"  #{item['number']} [{kind}, {item['state']}] {unwrap(item['title'])}")
        print(f"  {rate(f)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP stdio smoke test")
    parser.add_argument("query", nargs="?", default="How does the chunker avoid infinite loops?")
    parser.add_argument("--github-repo", default="fastapi/fastapi")
    parser.add_argument("--github-query", default="401 unauthorized bearer token")
    args = parser.parse_args()
    anyio.run(main, args.query, args.github_repo, args.github_query)
