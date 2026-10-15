"""Application settings, loaded from environment variables and an optional .env file."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """All tunable settings for the ingestion pipeline.

    Every field can be set via an environment variable of the same name
    (case-insensitive), e.g. ``CHUNK_SIZE=500``. List fields take JSON arrays.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    project_root: Path = Path(".")
    chunk_size: int = Field(default=1000, gt=0)
    chunk_overlap: int = Field(default=150, ge=0)
    max_file_size_mb: float = Field(default=2, gt=0)

    supported_extensions: list[str] = [".md", ".txt", ".py", ".json", ".yaml", ".yml", ".pdf"]
    # Directory names or glob patterns, e.g. "*.egg-info".
    excluded_dirs: list[str] = [
        ".git", ".venv", "venv", "env", "node_modules", "__pycache__", "dist", "build",
        ".mypy_cache", ".pytest_cache", ".idea", ".vscode", "chroma_db", "*.egg-info",
    ]
    # Glob patterns matched against the file *name* (case-insensitive).
    excluded_file_patterns: list[str] = [
        ".env", ".env.*", "*.pem", "*.key", "id_rsa*", "*secret*", "*credentials*", "*.p12",
    ]
    # File names that are always allowed, even if they match an excluded pattern.
    allowed_file_names: list[str] = [".env.example"]

    # ---- Phase 2: embeddings, vector store, retrieval ----
    # "fake" is a deterministic, offline provider intended for tests.
    embedding_provider: Literal["sentence_transformers", "fake"] = "sentence_transformers"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_batch_size: int = Field(default=32, gt=0)
    chroma_persist_dir: Path = Path("./chroma_db")
    chroma_collection_prefix: str = Field(default="project", max_length=20, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    retrieval_top_k: int = Field(default=5, gt=0)
    # Minimum similarity (1 - cosine distance) a result needs to be returned.
    retrieval_min_score: float = Field(default=0.0, ge=0.0, le=1.0)

    # ---- Phase 3: LLM + RAG ----
    # Validated when an LLM is actually needed (see app.llm.factory), so
    # ingestion/indexing work without any LLM configuration.
    llm_provider: Literal["anthropic", "openai_compatible", "fake"] = "anthropic"
    llm_model: str | None = None  # required unless llm_provider is "fake"
    llm_base_url: str | None = None  # openai_compatible only, e.g. http://localhost:11434/v1
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    llm_max_output_tokens: int = Field(default=1024, gt=0)
    # None = don't send a temperature (some reasoning models reject it).
    llm_temperature: float | None = Field(default=0.1, ge=0.0, le=2.0)
    llm_timeout_seconds: float = Field(default=60, gt=0)
    rag_max_context_chars: int = Field(default=12000, gt=0)

    # ---- Phase 4: MCP server ----
    # Comma-separated absolute paths; MCP tools only work on projects inside them.
    mcp_allowed_project_roots: str = ""
    mcp_max_file_chars: int = Field(default=20000, gt=0)
    mcp_max_top_k: int = Field(default=10, gt=0, le=100)

    # ---- Phase 5: GitHub (read-only) ----
    github_token: SecretStr | None = None  # optional; raises rate limits, enables private repos
    github_api_url: str = "https://api.github.com"
    github_default_repo: str | None = None  # "owner/name"
    github_allowed_repos: str = ""  # comma-separated "owner/name"; empty = only the default repo
    github_timeout_seconds: float = Field(default=20, gt=0)
    github_max_results: int = Field(default=20, gt=0, le=100)
    github_max_pages: int = Field(default=3, gt=0, le=10)
    github_max_body_chars: int = Field(default=4000, gt=0)
    github_max_comments: int = Field(default=10, ge=0, le=100)

    # ---- Phase 7: human-approved GitHub writes ----
    # A separate token and allowlist: writes never use GITHUB_TOKEN, reads never use this one.
    github_write_token: SecretStr | None = None
    github_write_enabled: bool = False
    github_write_allowed_repos: str = ""  # comma-separated owner/name; separate from GITHUB_ALLOWED_REPOS
    approval_ttl_seconds: int = Field(default=600, ge=30, le=3600)
    mcp_enable_write_tools: bool = False  # expose create_github_issue (with elicitation) to MCP clients
    audit_log_path: Path = Path("./audit/github_writes.jsonl")

    # ---- web app: local conversation history (SQLite, DATA_DIR/conversations.db) ----
    data_dir: Path = Path("./data")

    # ---- Phase 6: orchestration ----
    orch_max_tool_calls: int = Field(default=6, ge=1, le=50)  # tool executions per question
    orch_max_iterations: int = Field(default=4, ge=1, le=20)  # model turns that may call tools
    orch_max_github_calls: int = Field(default=3, ge=0, le=20)  # GitHub tool executions per question
    orch_tool_timeout_seconds: float = Field(default=30, gt=0)
    orch_memory_turns: int = Field(default=4, ge=0, le=20)  # previous Q&A turns kept as context

    @property
    def mcp_allowed_roots(self) -> list[Path]:
        """Parsed MCP_ALLOWED_PROJECT_ROOTS (not resolved or validated here)."""
        return [Path(p.strip()).expanduser() for p in self.mcp_allowed_project_roots.split(",") if p.strip()]

    @field_validator(
        "llm_model", "llm_base_url", "anthropic_api_key", "openai_api_key", "github_token", "github_default_repo",
        "github_write_token",
        mode="before",
    )
    @classmethod
    def _empty_is_none(cls, value: object) -> object:
        """Treat ``VAR=`` in .env as unset."""
        return None if isinstance(value, str) and not value.strip() else value

    @field_validator("llm_temperature", mode="before")
    @classmethod
    def _temperature_none(cls, value: object) -> object:
        if isinstance(value, str) and value.strip().lower() in ("", "none", "null"):
            return None
        return value

    @model_validator(mode="after")
    def _check_overlap(self) -> "Settings":
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError(
                f"CHUNK_OVERLAP ({self.chunk_overlap}) must be >= 0 and < CHUNK_SIZE ({self.chunk_size})"
            )
        return self

    @property
    def max_file_size_bytes(self) -> int:
        return int(self.max_file_size_mb * 1024 * 1024)


def get_settings(**overrides: object) -> Settings:
    """Build settings from the environment, with optional explicit overrides."""
    return Settings(**overrides)  # type: ignore[arg-type]
