"""Map internal exceptions to safe MCP tool errors.

Only ``ToolError`` messages reach the client. Every message here is written by
us: no absolute paths, stack traces, SDK messages or secrets.
"""

import logging
from collections.abc import Iterator
from contextlib import contextmanager

from mcp.server.mcpserver.exceptions import ToolError

from app.integrations.github_errors import GitHubError
from app.mcp_server.security import SecurityError
from app.retrieval.retriever import InvalidQueryError
from app.retrieval.vector_store import EmbeddingModelMismatchError, ProjectNotIndexedError, VectorStoreError

logger = logging.getLogger(__name__)


@contextmanager
def tool_errors(tool_name: str) -> Iterator[None]:
    """Translate known failures into ToolError; hide everything else."""
    try:
        yield
    except ToolError:
        raise
    except SecurityError as exc:
        raise ToolError(str(exc)) from None  # messages are written to be safe
    except InvalidQueryError as exc:
        raise ToolError(f"Invalid request: {exc}") from None
    except ProjectNotIndexedError:
        raise ToolError(
            "This project is not indexed yet. Index it first with: "
            "python -m app.cli index --path <project_path>"
        ) from None
    except EmbeddingModelMismatchError:
        raise ToolError(
            "The project's index was built with a different embedding model than the server uses. "
            "Rebuild it with: python -m app.cli index --path <project_path> --rebuild"
        ) from None
    except VectorStoreError:
        raise ToolError("The vector index could not be read.") from None
    except GitHubError as exc:
        # GitHub error messages are written by the client: no token, headers or raw responses.
        raise ToolError(str(exc)) from None
    except Exception as exc:  # unexpected: log the type server-side (stderr), reveal nothing
        logger.error("Tool %s failed with %s", tool_name, type(exc).__name__)
        raise ToolError(f"{tool_name} failed due to an internal error.") from None
