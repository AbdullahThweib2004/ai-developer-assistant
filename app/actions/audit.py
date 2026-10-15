"""Append-only audit log of GitHub write attempts (JSON lines).

Each record holds metadata only: timestamp, action, repo, draft hash, title length,
issue number/URL when created, and an error category. Never the body, the title
text or any token.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

logger = logging.getLogger(__name__)

AuditAction = Literal["approved", "cancelled", "created", "failed", "unknown", "blocked"]


class AuditRecord(BaseModel):
    timestamp: str
    action: AuditAction
    repo: str
    draft_hash: str
    title_length: int
    channel: str  # "cli" | "ui" | "mcp"
    issue_number: int | None = None
    issue_url: str | None = None
    error_category: str | None = None


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(
        self,
        action: AuditAction,
        repo: str,
        draft_hash: str,
        title_length: int,
        channel: str,
        issue_number: int | None = None,
        issue_url: str | None = None,
        error_category: str | None = None,
    ) -> AuditRecord:
        entry = AuditRecord(
            timestamp=datetime.now(UTC).isoformat(timespec="seconds"), action=action, repo=repo,
            draft_hash=draft_hash, title_length=title_length, channel=channel, issue_number=issue_number,
            issue_url=issue_url, error_category=error_category,
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(entry.model_dump_json(exclude_none=True) + "\n")
        logger.info("Audit: %s %s draft=%s", action, repo, draft_hash[:12])
        return entry

    def read(self) -> list[AuditRecord]:
        if not self.path.exists():
            return []
        return [AuditRecord.model_validate_json(line) for line in self.path.read_text().splitlines() if line.strip()]
