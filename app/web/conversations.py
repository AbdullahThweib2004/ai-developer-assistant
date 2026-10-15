"""Local conversation history for the web app (Python's built-in sqlite3).

Each conversation belongs to exactly one project (resolved path) and keeps its GitHub
repository and mode. Messages store the question, the structured answer (sections,
sources, trace metadata, token counts) and the outcome. Stored text is passed through
the same secret patterns as issue drafts and matches are redacted, so keys and tokens
are never written. Approvals are never stored; drafts aren't either.

The database is DATA_DIR/conversations.db (file mode 600). When "save conversation
history" is off, new conversations live in an in-memory database for this server run
only; existing saved ones stay readable until cleared.
"""

import json
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from app.actions.issue_draft import _ENV_SECRET_LINE, _PLACEHOLDER, _SECRET_PATTERNS

DB_NAME = "conversations.db"
TITLE_CHARS = 80
REDACTED = "[redacted]"
# KEY=value secrets anywhere in a line (the shared pattern only matches at line start); upper-case
# env-style names only, so prose like "the token: ..." is left alone.
_INLINE_ENV_SECRET = re.compile(
    r"\b([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY)[A-Z0-9_]*)"
    r"\s*[=:]\s*['\"]?([^\s'\"`]+)"
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    repo TEXT,
    mode TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created REAL NOT NULL,
    updated REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS conversations_project ON conversations (project, updated);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations (id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    data TEXT NOT NULL,
    created REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS messages_conversation ON messages (conversation_id, position);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def redact_secrets(text: str) -> str:
    """Replace anything the secret scanner recognizes (tokens, keys, KEY=value secrets)."""
    for _, pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)

    def env_line(match: re.Match[str]) -> str:
        value = match.group(2)
        return match.group(0) if _PLACEHOLDER.match(value) else match.group(0).replace(value, REDACTED)

    return _INLINE_ENV_SECRET.sub(env_line, _ENV_SECRET_LINE.sub(env_line, text))


def _redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, dict):
        return {k: _redact(v) for k, v in value.items()}
    return value


def _connect(target: str) -> sqlite3.Connection:
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_SCHEMA)
    return conn


class ConversationStore:
    def __init__(self, data_dir: Path) -> None:
        data_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(data_dir, 0o700)
        self.path = data_dir / DB_NAME
        if not self.path.exists():  # create it private before sqlite opens it
            os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        os.chmod(self.path, 0o600)
        self._disk = _connect(str(self.path))
        self._memory = _connect(":memory:")
        self._lock = threading.Lock()

    # ---- settings -------------------------------------------------------------------------

    @property
    def save_history(self) -> bool:
        row = self._disk.execute("SELECT value FROM meta WHERE key = 'save_history'").fetchone()
        return row is None or row["value"] == "1"

    def set_save_history(self, enabled: bool) -> None:
        with self._lock, self._disk:
            self._disk.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('save_history', ?)",
                               ("1" if enabled else "0",))

    # ---- conversations ---------------------------------------------------------------------

    def _where(self, conversation_id: str) -> sqlite3.Connection | None:
        for db in (self._memory, self._disk):
            if db.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
                return db
        return None

    @staticmethod
    def _out(row: sqlite3.Row, count: int) -> dict[str, Any]:
        return {"id": row["id"], "project": row["project"], "repo": row["repo"], "mode": row["mode"],
                "title": row["title"], "created": row["created"], "updated": row["updated"], "message_count": count}

    def create(self, project: Path, repo: str | None, mode: str) -> dict[str, Any]:
        now = time.time()
        conversation_id = secrets.token_urlsafe(12)
        db = self._disk if self.save_history else self._memory
        with self._lock, db:
            db.execute("INSERT INTO conversations (id, project, repo, mode, title, created, updated) "
                       "VALUES (?, ?, ?, ?, '', ?, ?)", (conversation_id, str(project), repo, mode, now, now))
        return self.get(conversation_id)  # type: ignore[return-value]

    def get(self, conversation_id: str, with_messages: bool = False) -> dict[str, Any] | None:
        db = self._where(conversation_id)
        if db is None:
            return None
        row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
        messages = [json.loads(m["data"]) for m in db.execute(
            "SELECT data FROM messages WHERE conversation_id = ? ORDER BY position", (conversation_id,))]
        out = self._out(row, len(messages))
        out["saved"] = db is self._disk
        if with_messages:
            out["messages"] = messages
        return out

    def for_project(self, project: Path) -> list[dict[str, Any]]:
        rows = []
        for db in (self._memory, self._disk):
            rows += [self._out(r, r["n"]) for r in db.execute(
                "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id = c.id) AS n "
                "FROM conversations c WHERE project = ?", (str(project),))]
        return sorted(rows, key=lambda c: c["updated"], reverse=True)

    def all_conversations(self) -> list[dict[str, Any]]:
        return [self._out(r, 0) for db in (self._memory, self._disk)
                for r in db.execute("SELECT * FROM conversations")]

    def rename(self, conversation_id: str, title: str) -> dict[str, Any] | None:
        db = self._where(conversation_id)
        if db is None:
            return None
        with self._lock, db:
            db.execute("UPDATE conversations SET title = ?, updated = ? WHERE id = ?",
                       (redact_secrets(title.strip())[:TITLE_CHARS * 2], time.time(), conversation_id))
        return self.get(conversation_id)

    def update_mode(self, conversation_id: str, mode: str) -> None:
        db = self._where(conversation_id)
        if db is not None:
            with self._lock, db:
                db.execute("UPDATE conversations SET mode = ? WHERE id = ?", (mode, conversation_id))

    def delete(self, conversation_id: str) -> bool:
        db = self._where(conversation_id)
        if db is None:
            return False
        with self._lock, db:
            db.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))
        return True

    def clear(self, project: Path | None = None) -> int:
        """Delete every conversation of ``project``, or all of them."""
        removed = 0
        for db in (self._memory, self._disk):
            with self._lock, db:
                if project is None:
                    removed += db.execute("DELETE FROM conversations").rowcount
                else:
                    removed += db.execute("DELETE FROM conversations WHERE project = ?", (str(project),)).rowcount
        if project is None:
            with self._lock:
                self._disk.execute("VACUUM")  # don't leave deleted text in free pages
        return removed

    # ---- messages ------------------------------------------------------------------------------

    def append(self, conversation_id: str, message: dict[str, Any]) -> None:
        """Store one finished turn (secrets redacted). Sets the title from the first question."""
        db = self._where(conversation_id)
        if db is None:
            return
        data = _redact(message)
        now = time.time()
        with self._lock, db:
            position = db.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = ?",
                                  (conversation_id,)).fetchone()[0]
            db.execute("INSERT INTO messages (id, conversation_id, position, data, created) VALUES (?, ?, ?, ?, ?)",
                       (data.get("id") or secrets.token_urlsafe(8), conversation_id, position, json.dumps(data), now))
            db.execute("UPDATE conversations SET updated = ?, title = CASE WHEN title = '' THEN ? ELSE title END "
                       "WHERE id = ?", (now, (data.get("question") or "")[:TITLE_CHARS], conversation_id))

    def memory(self, conversation_id: str, turns: int, answer_chars: int) -> list[tuple[str, str]]:
        """The last ``turns`` answered turns, as (question, answer) pairs for the investigator's memory."""
        conversation = self.get(conversation_id, with_messages=True)
        if conversation is None or turns <= 0:
            return []
        pairs = [(m["question"], ((m.get("answer") or {}).get("answer_markdown") or "")[:answer_chars])
                 for m in conversation["messages"] if m.get("status") == "done" and m.get("answer")]
        return pairs[-turns:]

    def close(self) -> None:
        self._disk.close()
        self._memory.close()
