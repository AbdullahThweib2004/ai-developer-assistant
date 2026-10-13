"""Smoke test: launch the MCP server over stdio and call a few tools.

Usage (from the repository root, after indexing this repository):
    uv run python -m app.cli index --path . --rebuild
    uv run python scripts/mcp_smoke_test.py ["your search query"]

The server runs as a subprocess, exactly as an MCP client (Inspector, Claude
Desktop, Cursor) would start it. Only the variables passed in ``env`` (plus a
minimal allow-list) reach it, so the allowed root and index location are set here.
"""

import json
import sys
from pathlib import Path

import anyio
from mcp import Client, StdioServerParameters

REPO = Path(__file__).resolve().parents[1]


async def main(query: str) -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        cwd=str(REPO),
        env={
            "MCP_ALLOWED_PROJECT_ROOTS": str(REPO),
            "CHROMA_PERSIST_DIR": str(REPO / "chroma_db"),
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
            print("\nsearch_project error:", " ".join(getattr(c, "text", "") for c in result.content))
            return
        data = result.structured_content
        print(f"\nsearch_project({query!r}) -> {len(data['hits'])} hits")
        for hit in data["hits"]:
            body = hit["content"].split("\n", 1)[1].rsplit("\n", 1)[0]  # strip the wrapper tags for display
            preview = " ".join(body.split())[:140]
            print(f"  {hit['rank']}. [{hit['score']:.3f}] {hit['file_path']} "
                  f"(chunk {hit['chunk_index']}, {hit['location']})\n     {preview}...")
        print(f"\nnote: {data['note']}")


if __name__ == "__main__":
    anyio.run(main, sys.argv[1] if len(sys.argv) > 1 else "How does the chunker avoid infinite loops?")
