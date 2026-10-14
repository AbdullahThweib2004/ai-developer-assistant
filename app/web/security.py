"""Local-only security for the web app.

The API runs on the developer's machine, but a browser can be tricked into
talking to it from other websites. Defenses, all on every request:

- **Host check**: only ``127.0.0.1:<port>``, ``localhost:<port>``, ``[::1]:<port>``
  (plus explicitly allowed hosts with --allow-network). Blocks DNS rebinding,
  where a hostile domain re-resolves to 127.0.0.1.
- **Origin check**: a request that carries an ``Origin`` (or ``Sec-Fetch-Site:
  cross-site``) from anywhere else is refused. No CORS headers are ever sent, so
  other origins can't read responses either.
- **Session token**: a random secret generated per launch, delivered only inside
  the served page. Every ``/api`` request must send it in ``X-Session-Token``.

Project paths go through the same allowlist and path checks as the MCP server.
Errors are mapped to safe messages: no stack traces, no paths outside the
allowed roots, no secrets.
"""

import logging
import secrets
from dataclasses import dataclass, field
from pathlib import Path

from app.config import Settings
from app.mcp_server.security import SecurityError, allowed_roots, resolve_project

logger = logging.getLogger(__name__)

TOKEN_HEADER = "X-Session-Token"
STATE_CHANGING = {"POST", "PUT", "PATCH", "DELETE"}


@dataclass
class LocalSecurity:
    port: int
    bind_host: str = "127.0.0.1"
    allow_network: bool = False
    extra_origins: list[str] = field(default_factory=list)  # e.g. the Vite dev server in --dev mode
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))

    @property
    def allowed_hosts(self) -> set[str]:
        hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}", f"[::1]:{self.port}"}
        if self.allow_network and self.bind_host not in ("0.0.0.0", "::"):
            hosts.add(f"{self.bind_host}:{self.port}")
        return hosts

    @property
    def allowed_origins(self) -> set[str]:
        return {f"http://{h}" for h in self.allowed_hosts} | set(self.extra_origins)

    def check(self, method: str, path: str, headers: dict[str, str]) -> tuple[int, str] | None:
        """Return (status, message) to reject the request, or None to allow it."""
        host = headers.get("host", "")
        if host not in self.allowed_hosts and not (self.allow_network and self.bind_host in ("0.0.0.0", "::")):
            return 421, "Invalid Host header."
        origin = headers.get("origin")
        if origin is not None and origin not in self.allowed_origins:
            return 403, "Cross-origin requests are not allowed."
        if headers.get("sec-fetch-site") == "cross-site":
            return 403, "Cross-site requests are not allowed."
        if path.startswith("/api/") or path == "/api":
            sent = headers.get(TOKEN_HEADER.lower(), "")
            if not sent or not secrets.compare_digest(sent, self.token):
                return 401, "Missing or invalid session token. Reload the page."
        return None


def validate_project(settings: Settings, path: str) -> Path:
    """Resolve a client-supplied project path inside MCP_ALLOWED_PROJECT_ROOTS."""
    return resolve_project(path, allowed_roots(settings))


__all__ = ["LocalSecurity", "SecurityError", "TOKEN_HEADER", "validate_project"]
