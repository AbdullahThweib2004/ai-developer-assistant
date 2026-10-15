"""Conversation history (SQLite): per-project storage, server-side project checks,
memory rebuilt from storage, secret redaction, no stored approvals."""

import os
import sqlite3
import stat
from pathlib import Path

import pytest

from app.config import Settings
from app.retrieval.embeddings import FakeEmbeddingProvider
from app.retrieval.indexer import build_index
from app.web.conversations import redact_secrets
from tests.helpers import GHP, SANDBOX, write_settings
from tests.test_web_api import QUESTION, SEARCH, Harness

FAKE_TOKEN = GHP + "Zq7" * 12  # assembled at runtime; looks like a real GitHub token


@pytest.fixture
def settings(index_settings: Settings, tmp_path: Path) -> Settings:
    root = index_settings.project_root
    return write_settings(root, audit_log_path=tmp_path / "audit" / "github_writes.jsonl", embedding_provider="fake",
                          chroma_persist_dir=index_settings.chroma_persist_dir, mcp_allowed_project_roots=str(root),
                          data_dir=tmp_path / "data")


@pytest.fixture
def indexed(settings: Settings) -> Settings:
    build_index(settings.project_root, settings=settings, provider=FakeEmbeddingProvider())
    return settings


def db_bytes(settings: Settings) -> bytes:
    return (settings.data_dir / "conversations.db").read_bytes()


def test_create_load_rename_delete(indexed: Settings) -> None:
    h = Harness(indexed, script=[SEARCH, "It compares hashes [S1]."])
    conversation = h.conversation(indexed.project_root)
    assert conversation["title"] == "" and conversation["mode"] == "project"
    h.investigate(conversation, QUESTION)
    loaded = h.get(f"/api/conversations/{conversation['id']}").json()
    assert loaded["title"] == QUESTION[:80] and loaded["saved"]
    message = loaded["messages"][0]
    assert message["question"] == QUESTION and message["status"] == "done"
    assert message["answer"]["project_sources"] and message["answer"]["totals"]["input_tokens"] >= 0
    assert message["answer"]["trace"] and all("content" not in t for t in message["answer"]["trace"])

    renamed = h.client.patch(f"/api/conversations/{conversation['id']}", json={"title": "Password checks"},
                             headers=h.headers).json()
    assert renamed["title"] == "Password checks"
    assert h.client.delete(f"/api/conversations/{conversation['id']}", headers=h.headers).json() == {"deleted": True}
    assert h.get(f"/api/conversations/{conversation['id']}").status_code == 404


def test_history_survives_a_server_restart(indexed: Settings) -> None:
    first = Harness(indexed, script=[SEARCH, "It compares hashes [S1]."])
    conversation = first.conversation(indexed.project_root)
    first.investigate(conversation, QUESTION)
    second = Harness(indexed)  # new app, same DATA_DIR
    listed = second.get("/api/conversations", params={"path": str(indexed.project_root)}).json()["conversations"]
    assert [c["id"] for c in listed] == [conversation["id"]] and listed[0]["message_count"] == 1


def test_conversations_are_listed_per_project_only(indexed: Settings) -> None:
    h = Harness(indexed)
    root, docs = indexed.project_root, indexed.project_root / "docs"
    a = h.conversation(root)
    b = h.conversation(docs)
    assert [c["id"] for c in h.get("/api/conversations", params={"path": str(root)}).json()["conversations"]] == [a["id"]]
    assert [c["id"] for c in h.get("/api/conversations", params={"path": str(docs)}).json()["conversations"]] == [b["id"]]
    assert h.client.delete("/api/conversations", params={"path": str(docs)}, headers=h.headers).json() == {"deleted": 1}
    assert h.get(f"/api/conversations/{a['id']}").status_code == 200


def test_project_mismatch_is_rejected_server_side(indexed: Settings) -> None:
    h = Harness(indexed, script=["should never run"])
    conversation = h.conversation(indexed.project_root)
    other = str(indexed.project_root / "docs")
    for url, body in [("/api/investigate", {"conversation_id": conversation["id"], "path": other, "question": "q"}),
                      ("/api/ask", {"conversation_id": conversation["id"], "path": other, "question": "q"})]:
        response = h.post(url, json=body)
        assert response.status_code == 409 and response.json()["detail"]["category"] == "conversation_mismatch"
    assert h.post("/api/investigate", json={"conversation_id": "nope", "path": str(indexed.project_root),
                                            "question": "q"}).status_code == 404
    assert h.llm.chat_calls == [] and h.get(f"/api/conversations/{conversation['id']}").json()["messages"] == []


def test_memory_is_rebuilt_from_storage_as_previous_conversation(indexed: Settings) -> None:
    first = Harness(indexed, script=[SEARCH, "It compares hashes with hmac.compare_digest [S1]."])
    conversation = first.conversation(indexed.project_root)
    first.investigate(conversation, QUESTION)

    second = Harness(indexed, script=[SEARCH, "Yes, see [S1]."])  # restarted server: no live session
    events = dict(second.investigate(conversation, "Is that timing-safe?"))
    prompt = second.llm.chat_calls[0].messages[0].content
    assert "<previous_conversation>" in prompt and QUESTION in prompt and "compare_digest" in prompt
    assert prompt.index("</previous_conversation>") < prompt.index("Question: Is that timing-safe?")
    # one turn rebuilt from storage + the turn just asked
    assert second.get(f"/api/sessions/{events['started']['session_id']}").json()["memory_turns"] == 2
    assert len(second.llm.chat_calls) == 2  # fresh evidence is still retrieved (search, then answer)


def test_secrets_are_never_stored(indexed: Settings) -> None:
    h = Harness(indexed, script=[SEARCH, f"The config contains {FAKE_TOKEN} and API_TOKEN=s3cr3tvalue123 [S1]."])
    conversation = h.conversation(indexed.project_root)
    answer = dict(h.investigate(conversation, f"Why is {FAKE_TOKEN} rejected?"))["answer"]
    assert FAKE_TOKEN in answer["answer_markdown"]  # the live answer is unchanged
    stored = h.get(f"/api/conversations/{conversation['id']}").json()
    raw = db_bytes(indexed)
    assert FAKE_TOKEN.encode() not in raw and b"s3cr3tvalue123" not in raw
    assert "[redacted]" in stored["messages"][0]["answer"]["answer_markdown"] and "[redacted]" in stored["title"]
    for configured in (indexed.github_token, indexed.github_write_token):  # the app's own tokens
        assert configured is None or configured.get_secret_value().encode() not in raw


def test_approvals_and_drafts_are_never_stored(indexed: Settings) -> None:
    h = Harness(indexed, script=[SEARCH, "It compares hashes [S1]."])
    conversation = h.conversation(indexed.project_root)
    events = dict(h.investigate(conversation, QUESTION))
    draft = h.post(f"/api/sessions/{events['answer']['session_id']}/drafts",
                   json={"turn_id": events["done"]["turn_id"]}).json()
    h.post(f"/api/drafts/{draft['id']}/approve", json={"draft_hash": draft["draft_hash"],
                                                       "typed_repo_confirmation": "wrong/repo"})
    raw = db_bytes(indexed)
    assert draft["id"].encode() not in raw and draft["draft_hash"].encode() not in raw
    tables = {r[0] for r in sqlite3.connect(indexed.data_dir / "conversations.db").execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert tables == {"conversations", "messages", "meta"}

    restarted = Harness(indexed)  # nothing to approve after a restart: drafts and approvals are gone
    assert restarted.get(f"/api/drafts/{draft['id']}").status_code == 404
    response = restarted.post(f"/api/drafts/{draft['id']}/approve",
                              json={"draft_hash": draft["draft_hash"], "typed_repo_confirmation": SANDBOX})
    assert response.status_code == 404 and restarted.posts() == []


def test_database_file_is_private(indexed: Settings) -> None:
    Harness(indexed).conversation(indexed.project_root)
    db = indexed.data_dir / "conversations.db"
    assert stat.S_IMODE(os.stat(db).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(indexed.data_dir).st_mode) == 0o700


def test_turning_history_off_keeps_new_chats_in_memory(indexed: Settings) -> None:
    h = Harness(indexed, script=["An answer."])
    assert h.client.put("/api/history/settings", json={"save_history": False}, headers=h.headers).json()["save_history"] is False
    conversation = h.conversation(indexed.project_root, mode="project")
    h.investigate(conversation, "A question that should not be saved")
    assert h.get(f"/api/conversations/{conversation['id']}").json()["saved"] is False
    assert b"should not be saved" not in db_bytes(indexed)
    assert Harness(indexed).get(f"/api/conversations/{conversation['id']}").status_code == 404


def test_clear_all_history(indexed: Settings) -> None:
    h = Harness(indexed, script=["An answer."])
    conversation = h.conversation(indexed.project_root)
    h.investigate(conversation, "Remember this question")
    assert h.client.delete("/api/history", headers=h.headers).json() == {"deleted": 1}
    assert b"Remember this question" not in db_bytes(indexed)


def test_failed_turns_are_stored_with_a_safe_error(settings: Settings) -> None:
    h = Harness(settings)  # not indexed
    conversation = h.conversation(settings.project_root)
    h.stream("/api/ask", {"conversation_id": conversation["id"], "path": str(settings.project_root), "question": "q"})
    message = h.get(f"/api/conversations/{conversation['id']}").json()["messages"][0]
    assert message["status"] == "error" and message["error"]["category"] == "not_indexed"


def test_redact_secrets_keeps_placeholders() -> None:
    assert redact_secrets(f"token {FAKE_TOKEN} end") == "token [redacted] end"
    assert redact_secrets("JWT_SECRET_KEY=change-me") == "JWT_SECRET_KEY=change-me"
    assert redact_secrets("API_TOKEN=abc123def456") == "API_TOKEN=[redacted]"
    assert redact_secrets("set API_TOKEN=abc123def456 first") == "set API_TOKEN=[redacted] first"
    assert redact_secrets("the bearer token: is case-sensitive") == "the bearer token: is case-sensitive"
