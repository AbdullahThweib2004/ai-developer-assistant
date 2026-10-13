"""Application settings, loaded from environment variables and an optional .env file."""

from pathlib import Path

from pydantic import Field, model_validator
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
