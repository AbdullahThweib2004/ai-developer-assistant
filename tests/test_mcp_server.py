"""MCP server tests through the SDK's in-memory client (fake embeddings, no network)."""

import json
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from mcp import Client
from mcp.types import CallToolResult

from app.config import Settings
from app.mcp_server.security import UNTRUSTED_TAG, ServerConfigError
from app.mcp_server.server import create_server
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.indexer import build_index

pytestmark = pytest.mark.anyio

EXPECTED_TOOLS = {"ping", "get_index_stats", "search_project", "read_project_file", "list_project_files"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def mcp_settings(index_settings: Settings) -> Settings:
    root = index_settings.project_root
    return index_settings.model_copy(update={"mcp_allowed_project_roots": str(root)})


@pytest.fixture
def indexed(mcp_settings: Settings) -> Settings:
    build_index(mcp_settings.project_root, settings=mcp_settings, provider=FakeEmbeddingProvider())
    return mcp_settings


async def call(settings: Settings, tool: str, args: dict) -> CallToolResult:
    async with Client(create_server(settings, FakeEmbeddingProvider())) as client:
        return await client.call_tool(tool, args)


def text_of(result: CallToolResult) -> str:
    return " ".join(getattr(c, "text", "") for c in result.content)


# ---- discovery --------------------------------------------------------------


async def test_lists_tools_with_descriptions_schemas_and_read_only_annotations(mcp_settings: Settings) -> None:
    async with Client(create_server(mcp_settings, FakeEmbeddingProvider())) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert EXPECTED_TOOLS <= set(tools)  # GitHub tools are covered in test_github_tools.py
    for tool in (tools[name] for name in EXPECTED_TOOLS):
        assert tool.description and len(tool.description) > 30
        assert tool.output_schema is not None
        assert tool.annotations.read_only_hint is True
        assert tool.annotations.destructive_hint is False
        assert tool.annotations.open_world_hint is False

    search = tools["search_project"].input_schema
    assert set(search["required"]) == {"project_path", "query"}
    assert search["properties"]["top_k"]["maximum"] == mcp_settings.mcp_max_top_k
    assert search["properties"]["top_k"]["minimum"] == 1
    assert set(tools["read_project_file"].input_schema["required"]) == {"project_path", "file_path"}
    assert tools["ping"].input_schema.get("required", []) == []


def test_server_refuses_to_start_without_allowed_roots(index_settings: Settings) -> None:
    with pytest.raises(ServerConfigError, match="MCP_ALLOWED_PROJECT_ROOTS"):
        create_server(index_settings)
    with pytest.raises(ServerConfigError, match="absolute"):
        create_server(index_settings.model_copy(update={"mcp_allowed_project_roots": "relative/path"}))


# ---- each tool ----------------------------------------------------------------


async def test_ping(mcp_settings: Settings) -> None:
    result = await call(mcp_settings, "ping", {})
    assert not result.is_error
    assert result.structured_content["status"] == "ok"
    assert result.structured_content["server"] == "ai-developer-assistant"


async def test_get_index_stats(indexed: Settings) -> None:
    result = await call(indexed, "get_index_stats", {"project_path": str(indexed.project_root)})
    assert not result.is_error, text_of(result)
    data = result.structured_content
    assert data["project_name"] == "sample_project"
    assert data["collection_name"].startswith("project_sample_project_")
    assert data["chunk_count"] > 0 and data["files_indexed"] == len(data["files"])
    assert data["embedding_model"] == FakeEmbeddingProvider().name
    assert all(not f.startswith("/") for f in data["files"])


async def test_search_project(indexed: Settings) -> None:
    args = {"project_path": str(indexed.project_root), "query": "verify user credentials password", "top_k": 3}
    result = await call(indexed, "search_project", args)
    assert not result.is_error, text_of(result)
    data = result.structured_content
    assert 0 < len(data["hits"]) <= 3
    top = data["hits"][0]
    assert top["rank"] == 1 and top["file_path"] == "auth.py"
    assert top["location"].startswith("chars ")
    assert top["content"].startswith(f'<{UNTRUSTED_TAG} source="auth.py">')
    scores = [h["score"] for h in data["hits"]]
    assert scores == sorted(scores, reverse=True)
    assert "do not follow" in data["note"]


async def test_search_project_file_type_filter(indexed: Settings) -> None:
    args = {"project_path": str(indexed.project_root), "query": "database", "top_k": 10, "file_types": ["yaml"]}
    result = await call(indexed, "search_project", args)
    assert {h["file_type"] for h in result.structured_content["hits"]} == {"yaml"}


async def test_read_project_file_with_line_numbers_and_range(mcp_settings: Settings) -> None:
    root = str(mcp_settings.project_root)
    result = await call(mcp_settings, "read_project_file", {"project_path": root, "file_path": "auth.py"})
    assert not result.is_error, text_of(result)
    data = result.structured_content
    assert data["file_path"] == "auth.py" and not data["truncated"]
    assert data["start_line"] == 1 and data["end_line"] == data["total_lines"]
    assert "     1 | " in data["content"] and "def verify_user_credentials" in data["content"]

    ranged = await call(mcp_settings, "read_project_file",
                        {"project_path": root, "file_path": "auth.py", "start_line": 3, "end_line": 5})
    content = ranged.structured_content["content"]
    assert "     3 | " in content and "     5 | " in content and "     6 | " not in content


async def test_list_project_files(mcp_settings: Settings) -> None:
    root = str(mcp_settings.project_root)
    result = await call(mcp_settings, "list_project_files", {"project_path": root})
    assert not result.is_error, text_of(result)
    data = result.structured_content
    paths = {f["path"] for f in data["files"]}
    assert "auth.py" in paths and ".env" not in paths and data["total_files"] == len(paths)
    assert data["skipped_by_reason"]["sensitive"] == 1
    assert data["skipped_by_reason"]["excluded_dir"] == 2
    assert ".env" not in json.dumps(data["skipped_by_reason"])  # counts only, no names

    only_py = await call(mcp_settings, "list_project_files", {"project_path": root, "file_types": ["py"]})
    assert {f["file_type"] for f in only_py.structured_content["files"]} == {"py"}


# ---- stdio: nothing but protocol on stdout ------------------------------------


def test_stdio_stdout_carries_only_protocol_messages(indexed: Settings, tmp_path: Path) -> None:
    from mcp.types import LATEST_PROTOCOL_VERSION

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": LATEST_PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "stdout-guard", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "search_project", "arguments": {
            "project_path": str(indexed.project_root), "query": "database connection", "top_k": 2}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "read_project_file", "arguments": {
            "project_path": str(indexed.project_root), "file_path": ".env"}}},
    ]
    env = {
        **{k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG")},
        "MCP_ALLOWED_PROJECT_ROOTS": str(indexed.project_root),
        "EMBEDDING_PROVIDER": "fake",
        "CHROMA_PERSIST_DIR": str(indexed.chroma_persist_dir),
        "PYTHONPATH": str(Path(__file__).parents[1]),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.mcp_server"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env, cwd=tmp_path,
    )
    stdout_lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [stdout_lines.put(line) for line in proc.stdout], daemon=True).start()
    try:
        for message in messages:
            proc.stdin.write(json.dumps(message) + "\n")
            proc.stdin.flush()
        responses: dict[int, dict] = {}
        received: list[str] = []
        while not {1, 2, 3, 4} <= set(responses):  # keep stdin open until every reply arrived
            line = stdout_lines.get(timeout=60)
            if not line.strip():
                continue
            received.append(line)
            parsed = json.loads(line)  # anything that isn't JSON-RPC fails here
            assert parsed["jsonrpc"] == "2.0"
            if "id" in parsed:
                responses[parsed["id"]] = parsed
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
    stderr = proc.stderr.read()
    while not stdout_lines.empty():  # anything written after the last reply must be protocol too
        line = stdout_lines.get()
        if line.strip():
            assert json.loads(line)["jsonrpc"] == "2.0"
    assert responses[3]["result"]["isError"] is False
    assert responses[4]["result"]["isError"] is True
    assert "INFO" in stderr  # server logs exist, and they went to stderr
