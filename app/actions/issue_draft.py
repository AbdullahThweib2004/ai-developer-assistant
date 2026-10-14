"""GitHub issue drafts: building, sanitizing and validating them before a human sees them.

A draft is inert data. Nothing here talks to GitHub. Every draft goes through the
same checks whether the LLM proposed it, the user wrote it, or it was edited:

- repo in the write allowlist; title 1-256 chars without newlines; body within
  GitHub's limit; labels optional and validated
- secret scan (blocks the draft and reports only the *kind* of secret found)
- absolute local paths -> project-relative (or redacted)
- @mentions wrapped in backticks so nobody gets pinged
- closing keywords ("fixes #12") rewritten so the issue can't close anything
- internal <untrusted-...> wrapper tags removed
- tool/AI credit lines removed

``draft_hash`` (sha256 of repo, title, body, labels) binds an approval to the exact
content. Any edit changes it.
"""

import hashlib
import json
import math
import re
import uuid
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from app.config import Settings
from app.integrations.github_errors import GitHubError
from app.integrations.github_write_client import resolve_write_repo

TITLE_MAX = 256
BODY_MAX = 65_536  # GitHub's issue body limit
MAX_LABELS = 10
LABEL_PATTERN = re.compile(r"^[^,\n\r]{1,50}$")


class EvidenceRef(BaseModel):
    """A source the draft is based on (from the investigation's S#/G# citations)."""

    cite_id: str
    kind: str  # "project" | "github"
    label: str
    file_path: str | None = None
    location: str | None = None
    url: str | None = None
    number: int | None = None


class IssueDraft(BaseModel):
    draft_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:12])
    repo: str
    title: str
    body: str
    labels: list[str] = Field(default_factory=list)
    source_question: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    draft_hash: str = ""
    errors: list[str] = Field(default_factory=list)  # block approval
    warnings: list[str] = Field(default_factory=list)  # shown to the human; don't block

    @property
    def valid(self) -> bool:
        return not self.errors


def compute_hash(repo: str, title: str, body: str, labels: list[str]) -> str:
    payload = json.dumps([repo.lower(), title, body, sorted(labels)], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


# ---- secret scanning -----------------------------------------------------------------

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("private key block", re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    ("GitHub fine-grained token", re.compile(r"github_(?:pat)_[A-Za-z0-9_]{20,}")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("API key (sk-...)", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("AWS secret access key", re.compile(r"(?i)aws_secret_access_key\s*[=:]\s*\S{16,}")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
]
_ENV_SECRET_LINE = re.compile(
    r"(?im)^\s*(?:export\s+)?([A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|PASSWD|API_KEY|APIKEY|PRIVATE_KEY|ACCESS_KEY)"
    r"[A-Z0-9_]*)\s*[=:]\s*['\"]?([^\s'\"]+)"
)
_PLACEHOLDER = re.compile(
    r"(?i)^(?:change-?me|changeme|x{3,}|\*{3,}|\.{3}|<.*>|\$\{?\w+\}?|your[-_].*|example.*|placeholder|dummy|"
    r"test|none|null|redacted|\[redacted\])$"
)
_CANDIDATE = re.compile(r"[A-Za-z0-9+/=_-]{32,}")


def _entropy(text: str) -> float:
    counts = Counter(text)
    return -sum(c / len(text) * math.log2(c / len(text)) for c in counts.values())


def scan_secrets(text: str) -> list[str]:
    """Kinds of secrets that appear in ``text``. Never returns the values."""
    kinds: list[str] = []
    for kind, pattern in _SECRET_PATTERNS:
        if pattern.search(text) and kind not in kinds:
            kinds.append(kind)
    for match in _ENV_SECRET_LINE.finditer(text):
        if not _PLACEHOLDER.match(match.group(2)) and "secret-like KEY=value line" not in kinds:
            kinds.append("secret-like KEY=value line")
    for match in _CANDIDATE.finditer(text):
        token = match.group(0)
        if re.fullmatch(r"[0-9a-fA-F]+", token):  # hashes and commit SHAs are fine
            continue
        if re.search(r"[A-Za-z]", token) and re.search(r"\d", token) and _entropy(token) >= 4.2:
            if "long high-entropy string" not in kinds:
                kinds.append("long high-entropy string")
    return kinds


# ---- sanitizing ------------------------------------------------------------------------

_UNTRUSTED_OPEN = re.compile(r"<untrusted-[a-z-]+(?:\s+source=\"[^\"]*\")?>\n?", re.IGNORECASE)
_UNTRUSTED_CLOSE = re.compile(r"\n?</untrusted-[a-z-]+>", re.IGNORECASE)
_MENTION = re.compile(r"(?<![\w/@.])@([A-Za-z0-9](?:[A-Za-z0-9-]{0,38}[A-Za-z0-9])?)\b")
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)  # fenced blocks and inline code
_CLOSING = re.compile(
    r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b(:?\s+)"
    r"((?:[A-Za-z0-9-]+/[A-Za-z0-9._-]+)?#\d+|https://github\.com/[\w.-]+/[\w.-]+/issues/\d+)",
    re.IGNORECASE,
)
_LOCAL_PATH = re.compile(r"(?:(?<![\w./:-])/(?:home|Users|root|tmp|var/folders|mnt|private)/[^\s`'\")\]]+"
                         r"|\b[A-Za-z]:\\\\?Users\\[^\s`'\")\]]+)")
_CREDIT_LINE = re.compile(
    r"(?im)^.*\b(?:generated (?:with|by)|created (?:with|by) (?:an? )?(?:ai|llm|assistant)|co-authored-by|"
    r"written by (?:an? )?(?:ai|llm|assistant)|ai-generated)\b.*$\n?"
)


def _outside_code(text: str, transform) -> str:  # type: ignore[no-untyped-def]
    """Apply ``transform`` only to text outside code blocks and inline code."""
    out, last = [], 0
    for match in _CODE.finditer(text):
        out.append(transform(text[last:match.start()]))
        out.append(match.group(0))
        last = match.end()
    out.append(transform(text[last:]))
    return "".join(out)


def sanitize_text(text: str, project_root: Path | None = None) -> tuple[str, list[str]]:
    """Make text safe to publish. Returns (text, notes about what changed)."""
    notes: list[str] = []
    original = text
    text = _UNTRUSTED_CLOSE.sub("", _UNTRUSTED_OPEN.sub("", text))
    if text != original:
        notes.append("Removed internal untrusted-content tags.")

    if project_root is not None:
        root = str(project_root.resolve())
        if root in text:
            text = text.replace(root + "/", "").replace(root, ".")
            notes.append("Replaced absolute project paths with project-relative paths.")

    def redact(match: re.Match[str]) -> str:
        return f"<local-path>/{Path(match.group(0).replace(chr(92), '/')).name}"

    redacted = _LOCAL_PATH.sub(redact, text)
    if redacted != text:
        notes.append("Redacted absolute local paths.")
        text = redacted

    neutral = _outside_code(text, lambda t: _CLOSING.sub(lambda m: f"related to{m.group(2)}{m.group(3)}", t))
    if neutral != text:
        notes.append("Rewrote closing keywords (e.g. 'fixes #12' -> 'related to #12') so the issue closes nothing.")
        text = neutral

    quiet = _outside_code(text, lambda t: _MENTION.sub(lambda m: f"`@{m.group(1)}`", t))
    if quiet != text:
        notes.append("Wrapped @mentions in backticks so nobody is notified.")
        text = quiet

    uncredited = _CREDIT_LINE.sub("", text)
    if uncredited != text:
        notes.append("Removed tool/AI credit lines.")
        text = uncredited
    return text.strip(), notes


# One or more adjacent citation groups, with the spaces before them: " [S1][S2, G3]".
_CITATIONS = re.compile(r"(\s*)((?:\[\s*[SG]\d+(?:\s*[,;]\s*[SG]\d+)*\s*\])+)")
_CITATION_ID = re.compile(r"[SG]\d+")


def resolve_citations(text: str, refs: list[EvidenceRef]) -> str:
    """Replace internal citation ids with references that mean something on GitHub.

    [S#] -> `path/to/file` and [G#] -> owner/repo#N (GitHub auto-links it). Unknown ids are dropped.
    """
    by_id = {r.cite_id: r for r in refs}

    def render(cid: str) -> str | None:
        ref = by_id.get(cid)
        if ref is None:
            return None
        if ref.kind == "project" and ref.file_path:
            return f"`{ref.file_path}`"
        if ref.kind == "github" and ref.number is not None:
            repo = ref.label.split("#", 1)[0] if "#" in ref.label else ""
            return f"{repo}#{ref.number}" if repo else f"#{ref.number}"
        return None

    def replace(match: re.Match[str]) -> str:
        parts = list(dict.fromkeys(p for p in (render(c) for c in _CITATION_ID.findall(match.group(2))) if p))
        # Unknown ids vanish together with the spaces in front of them; nothing else is touched.
        return f"{match.group(1)}({', '.join(parts)})" if parts else ""

    return _CITATIONS.sub(replace, text)


# ---- body template --------------------------------------------------------------------


def _section(title: str, content: str | list[str] | None) -> str:
    if not content:
        return ""
    if isinstance(content, list):
        items = [c.strip() for c in content if c and c.strip()]
        if not items:
            return ""
        content = "\n".join(f"- {c}" for c in items)
    return f"## {title}\n\n{content.strip()}\n"


def render_body(
    summary: str,
    steps_to_reproduce: list[str] | str | None = None,
    expected_behavior: str | None = None,
    actual_behavior: str | None = None,
    error_messages: str | None = None,
    relevant_files: list[str] | None = None,
    related: list[EvidenceRef] | None = None,
    investigation_steps: list[str] | str | None = None,
) -> str:
    related_lines = []
    for ref in related or []:
        title = sanitize_text(ref.label)[0]
        related_lines.append(f"[#{ref.number}]({ref.url}) (lead, not confirmed)\n  > {title}")
    errors_block = f"```\n{error_messages.strip()}\n```" if error_messages and error_messages.strip() else None
    parts = [
        _section("Summary", summary),
        _section("Steps to reproduce", steps_to_reproduce),
        _section("Expected behavior", expected_behavior),
        _section("Actual behavior", actual_behavior),
        _section("Error messages", errors_block),
        _section("Relevant files", [f"`{f}`" for f in (relevant_files or [])]),
        _section("Related issues/PRs (leads, not confirmed)", related_lines),
        _section("Proposed investigation steps", investigation_steps),
    ]
    return "\n".join(p for p in parts if p).strip() + "\n"


# ---- validation ----------------------------------------------------------------------------


def finalize(settings: Settings, draft: IssueDraft, project_root: Path | None = None) -> IssueDraft:
    """Sanitize and validate a draft in place (title, body, labels, repo) and recompute its hash.

    Used for new drafts and after every human edit.
    """
    errors: list[str] = []
    warnings: list[str] = []

    title, title_notes = sanitize_text(draft.title, project_root)
    title = " ".join(title.split())  # no newlines in titles
    body, body_notes = sanitize_text(draft.body, project_root)
    warnings += list(dict.fromkeys(title_notes + body_notes))

    try:
        draft.repo = resolve_write_repo(settings, draft.repo).full_name
    except GitHubError as exc:
        errors.append(str(exc))
    if not title:
        errors.append("The title is empty.")
    elif len(title) > TITLE_MAX:
        errors.append(f"The title is longer than {TITLE_MAX} characters.")
    if len(body) > BODY_MAX:
        errors.append(f"The body is longer than GitHub's limit of {BODY_MAX} characters.")
    labels = [label.strip() for label in draft.labels if label.strip()]
    if len(labels) > MAX_LABELS:
        errors.append(f"At most {MAX_LABELS} labels are allowed.")
    for label in labels:
        if not LABEL_PATTERN.match(label):
            errors.append("Labels must be 1-50 characters without commas or newlines.")
            break
    for kind in scan_secrets(f"{title}\n{body}\n{' '.join(labels)}"):
        errors.append(f"Possible secret found ({kind}). Remove it before this issue can be created.")

    draft.title, draft.body, draft.labels = title, body, labels
    draft.errors, draft.warnings = errors, warnings
    draft.draft_hash = compute_hash(draft.repo, title, body, labels)
    return draft


def build_draft(
    settings: Settings,
    repo: str,
    title: str,
    summary: str,
    *,
    steps_to_reproduce: list[str] | str | None = None,
    expected_behavior: str | None = None,
    actual_behavior: str | None = None,
    error_messages: str | None = None,
    relevant_files: list[str] | None = None,
    related: list[EvidenceRef] | None = None,
    investigation_steps: list[str] | str | None = None,
    labels: list[str] | None = None,
    source_question: str = "",
    evidence: list[EvidenceRef] | None = None,
    project_root: Path | None = None,
) -> IssueDraft:
    refs = evidence or []

    def cite(value):  # type: ignore[no-untyped-def]
        if isinstance(value, list):
            return [resolve_citations(str(v), refs) for v in value]
        return resolve_citations(value, refs) if isinstance(value, str) else value

    body = render_body(cite(summary), cite(steps_to_reproduce), cite(expected_behavior), cite(actual_behavior),
                       error_messages, relevant_files, related, cite(investigation_steps))
    title = resolve_citations(title, refs)
    draft = IssueDraft(repo=repo, title=title, body=body, labels=labels or [], source_question=source_question,
                       evidence=evidence or [])
    return finalize(settings, draft, project_root)


def manual_draft(settings: Settings, repo: str, title: str, body: str, labels: list[str] | None = None,
                 project_root: Path | None = None) -> IssueDraft:
    """A draft written by the user (CLI/UI). Same checks as model-proposed drafts."""
    return finalize(settings, IssueDraft(repo=repo, title=title, body=body, labels=labels or []), project_root)
