"""Where the orchestrator's tools run: in-process or through a real MCP session.

Both backends expose the same normalized interface (``list_tools`` /
``call_tool`` -> ``ToolResult``) and are backed by the same ``MCPServer``
object (``app.mcp_server.server.create_server``), so they behave identically:

- ``InProcessBackend`` calls the server object directly (fast, for tests).
- ``MCPClientBackend`` starts ``python -m app.mcp_server`` as a subprocess and
  calls it over stdio with the official MCP client: the real protocol path,
  used by the CLI and UI.
"""

import json
import logging
import re
import shutil
import sys
import tempfile
from functools import partial
from pathlib import Path
from typing import Any, Protocol

import anyio
import httpx
from anyio.from_thread import start_blocking_portal
from mcp import Client, StdioServerParameters, stdio_client
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from mcp.types import Tool as MCPTool
from pydantic import BaseModel, SecretStr

from app.config import Settings
from app.retrieval.embeddings import EmbeddingProvider

logger = logging.getLogger(__name__)

# Settings never forwarded to the MCP server process: it makes no LLM calls.
_NOT_FORWARDED = {
    "project_root", "mcp_allowed_project_roots", "llm_provider", "llm_model", "llm_base_url",
    "anthropic_api_key", "openai_api_key", "llm_max_output_tokens", "llm_temperature", "llm_timeout_seconds",
}
_ERROR_PREFIX = re.compile(r"^Error executing tool [\w-]+: ")


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    open_world: bool


class ToolResult(BaseModel):
    """A normalized tool outcome: structured data, or a safe error message."""

    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None


class ToolBackend(Protocol):
    def list_tools(self) -> list[ToolDefinition]: ...

    def call_tool(self, name: str, arguments: dict[str, Any], timeout_seconds: float) -> ToolResult: ...

    def close(self) -> None: ...


# ---- normalization shared by both backends ------------------------------------------


def to_definition(tool: MCPTool) -> ToolDefinition:
    annotations = tool.annotations
    return ToolDefinition(
        name=tool.name,
        description=tool.description or "",
        input_schema=tool.input_schema,
        read_only=bool(annotations and annotations.read_only_hint),
        open_world=bool(annotations and annotations.open_world_hint),
    )


def error_message(text: str) -> str:
    """Drop the SDK's "Error executing tool X: " prefix so both backends report the same text."""
    return _ERROR_PREFIX.sub("", text.strip())


def from_call_result(result: CallToolResult) -> ToolResult:
    if result.is_error:
        text = " ".join(getattr(c, "text", "") for c in result.content)
        return ToolResult(ok=False, error=error_message(text) or "Tool failed.")
    data = result.structured_content
    if data is None:  # text-only result: keep it as data
        data = {"text": " ".join(getattr(c, "text", "") for c in result.content)}
    return ToolResult(ok=True, data=json.loads(json.dumps(data, default=str)))


# ---- in-process -------------------------------------------------------------------------


class InProcessBackend:
    """Calls the MCP server object directly, without a transport."""

    def __init__(
        self,
        settings: Settings,
        project_path: Path,
        embedding_provider: EmbeddingProvider | None = None,
        github_transport: httpx.BaseTransport | None = None,
    ) -> None:
        from app.mcp_server.server import create_server

        server_settings = settings.model_copy(update={"mcp_allowed_project_roots": str(project_path.resolve())})
        self._server = create_server(server_settings, embedding_provider, github_transport=github_transport)

    def list_tools(self) -> list[ToolDefinition]:
        return [to_definition(t) for t in anyio.run(self._server.list_tools)]

    def call_tool(self, name: str, arguments: dict[str, Any], timeout_seconds: float) -> ToolResult:
        # Note: tools run synchronously in-process, so the timeout is not enforced here;
        # MCPClientBackend enforces it on the wire.
        try:
            result = anyio.run(partial(self._server.call_tool, name, arguments))
        except ToolError as exc:
            return ToolResult(ok=False, error=error_message(str(exc)))
        return from_call_result(result)

    def close(self) -> None:
        pass


# ---- real MCP over stdio ---------------------------------------------------------------------


def server_env(settings: Settings, project_path: Path) -> dict[str, str]:
    """Environment for the MCP server process: everything it needs, no LLM settings or keys.

    MCP_ALLOWED_PROJECT_ROOTS is the session's project only, so the server can't
    read any other project even if asked to.
    """
    env: dict[str, str] = {}
    for name in Settings.model_fields:
        if name in _NOT_FORWARDED:
            continue
        value = getattr(settings, name)
        if value is None:
            continue
        if isinstance(value, SecretStr):
            value = value.get_secret_value()
        elif isinstance(value, Path):
            value = str(value.resolve())
        elif isinstance(value, list):
            value = json.dumps([str(v) for v in value])
        env[name.upper()] = str(value)
    env["MCP_ALLOWED_PROJECT_ROOTS"] = str(project_path.resolve())
    return env


class MCPClientBackend:
    """Starts our MCP server over stdio and calls tools through an MCP client session.

    The server runs in an empty temporary working directory, so it reads only
    the environment passed here (no .env with LLM keys), and its logs go to a
    file there instead of the terminal.
    """

    def __init__(self, settings: Settings, project_path: Path) -> None:
        self._workdir = Path(tempfile.mkdtemp(prefix="ai-dev-assistant-mcp-"))
        self.server_log = self._workdir / "server.log"
        self._log_file = self.server_log.open("w", encoding="utf-8")
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "app.mcp_server"],
            env=server_env(settings, project_path), cwd=str(self._workdir),
        )
        self._portal_cm = start_blocking_portal()
        self._portal = self._portal_cm.__enter__()
        try:
            self._client_cm = self._portal.wrap_async_context_manager(
                Client(stdio_client(params, errlog=self._log_file))
            )
            self._client: Client = self._client_cm.__enter__()
        except BaseException:
            self._portal_cm.__exit__(None, None, None)
            self._log_file.close()
            raise

    def list_tools(self) -> list[ToolDefinition]:
        result = self._portal.call(self._client.list_tools)
        return [to_definition(t) for t in result.tools]

    def call_tool(self, name: str, arguments: dict[str, Any], timeout_seconds: float) -> ToolResult:
        try:
            result = self._portal.call(
                partial(self._client.call_tool, name, arguments, read_timeout_seconds=timeout_seconds)
            )
        except Exception as exc:  # timeouts and protocol errors: report the type only
            if "timeout" in type(exc).__name__.lower() or "timed out" in str(exc).lower():
                return ToolResult(ok=False, error=f"Tool timed out after {timeout_seconds:.0f}s.")
            logger.warning("MCP call %s failed: %s", name, type(exc).__name__)
            return ToolResult(ok=False, error=f"Tool call failed ({type(exc).__name__}).")
        return from_call_result(result)

    def close(self) -> None:
        try:
            self._client_cm.__exit__(None, None, None)
        finally:
            self._portal_cm.__exit__(None, None, None)
            self._log_file.close()
            shutil.rmtree(self._workdir, ignore_errors=True)

    def __enter__(self) -> "MCPClientBackend":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
