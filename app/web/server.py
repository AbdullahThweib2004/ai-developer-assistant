"""The local web app: API + built frontend on one port.

    uv run ai-dev-assistant web [--port 8765] [--demo] [--dev] [--allow-network --host 0.0.0.0]
    uv run python -m app.web ...

Binds to 127.0.0.1 only. Serving on another interface needs --allow-network
(with a loud warning). A random session token is generated per launch and
injected into the served page; every /api request must carry it.
"""

import argparse
import html
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import get_settings
from app.web.api import build_router
from app.web.security import LocalSecurity
from app.web.sessions import Runtime, SessionManager, real_runtime

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIST = REPO_ROOT / "frontend" / "dist"
BUILD_COMMAND = "cd frontend && npm install && npm run build"
TOKEN_META = '<meta name="ai-dev-assistant-token" content="{token}">'
DEV_ORIGIN = "http://localhost:5173"


def create_app(runtime: Runtime, security: LocalSecurity, frontend_dist: Path = FRONTEND_DIST) -> FastAPI:
    sessions = SessionManager(runtime)

    @asynccontextmanager
    async def lifespan(app: FastAPI):  # type: ignore[no-untyped-def]
        yield
        sessions.close_all()  # stop per-session MCP server processes

    app = FastAPI(title="AI Developer Assistant", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.sessions = sessions
    app.state.security = security

    @app.middleware("http")
    async def local_only(request: Request, call_next):  # type: ignore[no-untyped-def]
        headers = {k.lower(): v for k, v in request.headers.items()}
        rejection = security.check(request.method, request.url.path, headers)
        if rejection is not None:
            status, message = rejection
            return JSONResponse({"detail": {"category": "forbidden", "message": message, "hint": None}},
                                status_code=status)
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_errors(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, dict) else {"category": "error", "message": str(exc.detail),
                                                                  "hint": None}
        return JSONResponse({"detail": detail}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_errors(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = sorted({".".join(str(p) for p in e.get("loc", [])[1:]) for e in exc.errors()})
        return JSONResponse({"detail": {"category": "invalid_request",
                                        "message": f"Invalid request: {', '.join(fields) or 'body'}.", "hint": None}},
                            status_code=422)

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.error("Unhandled %s", type(exc).__name__)  # type only: no traceback or values to the client
        return JSONResponse({"detail": {"category": "internal", "message": "Something went wrong.",
                                        "hint": "Check the server log."}}, status_code=500)

    app.include_router(build_router(sessions))

    index_file = frontend_dist / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str) -> Response:
        if path.startswith("api/"):
            return JSONResponse({"detail": {"category": "not_found", "message": "Not found.", "hint": None}}, 404)
        candidate = (frontend_dist / path).resolve()
        if path and candidate.is_file() and candidate.is_relative_to(frontend_dist.resolve()):
            return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable"}
                                if path.startswith("assets/") else None)
        if not index_file.is_file():
            return HTMLResponse(
                "<!doctype html><title>AI Developer Assistant</title><body style='font-family:sans-serif;padding:2rem'>"
                "<h1>Frontend not built</h1><p>Run:</p><pre>" + html.escape(BUILD_COMMAND) + "</pre></body>", 503)
        page = index_file.read_text(encoding="utf-8")
        page = page.replace("</head>", TOKEN_META.format(token=html.escape(security.token)) + "</head>", 1)
        return HTMLResponse(page, headers={"Cache-Control": "no-store"})

    return app


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ai-dev-assistant web", description="Run the local web app.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-network", action="store_true",
                        help="Allow binding to a non-loopback address (exposes the app on your network)")
    parser.add_argument("--demo", action="store_true", help="Fake LLM, embeddings and GitHub (no network, no cost)")
    parser.add_argument("--dev", action="store_true",
                        help=f"Allow the Vite dev server ({DEV_ORIGIN}) and write its token to frontend/.env.development.local")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    import uvicorn

    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "huggingface_hub", "mcp", "app.actions.audit"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    loopback = args.host in ("127.0.0.1", "localhost", "::1")
    if not loopback and not args.allow_network:
        print(f"Refusing to bind to {args.host}: the app is local-only. Pass --allow-network to expose it "
              "on your network (not recommended).", file=sys.stderr)
        raise SystemExit(2)
    if not loopback:
        print("\n" + "!" * 78 + f"\nWARNING: serving on {args.host}. Anyone who can reach this machine and obtain "
              "the page can use\nyour LLM key and approve GitHub writes. Use only on a trusted network.\n"
              + "!" * 78 + "\n", file=sys.stderr)

    if args.demo:
        from app.web.demo import demo_runtime

        runtime = demo_runtime(REPO_ROOT)
    else:
        runtime = real_runtime(get_settings())

    security = LocalSecurity(port=args.port, bind_host=args.host, allow_network=args.allow_network,
                             extra_origins=[DEV_ORIGIN] if args.dev else [])
    if args.dev:
        dev_env = REPO_ROOT / "frontend" / ".env.development.local"  # gitignored (.env.*)
        dev_env.write_text(f"VITE_SESSION_TOKEN={security.token}\nVITE_API_PORT={args.port}\n", encoding="utf-8")
        print(f"Dev mode: wrote the session token for Vite to {dev_env.relative_to(REPO_ROOT)}. "
              "Start the frontend with: cd frontend && npm run dev")
    if not (FRONTEND_DIST / "index.html").is_file():
        print(f"Frontend not built. Build it with:\n    {BUILD_COMMAND}\n(the API still runs)")

    app = create_app(runtime, security)
    shown = "127.0.0.1" if loopback else args.host
    print(f"\nAI Developer Assistant{' (demo mode)' if args.demo else ''}: http://{shown}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


def cli() -> None:
    """``ai-dev-assistant web ...`` console entry point."""
    argv = sys.argv[1:]
    if not argv or argv[0] != "web":
        print("usage: ai-dev-assistant web [--demo] [--port 8765] [--dev] [--allow-network --host HOST]",
              file=sys.stderr)
        raise SystemExit(2)
    main(argv[1:])
