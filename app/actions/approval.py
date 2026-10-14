"""Human approval for GitHub issue creation. App code only: no LLM tool reaches this.

The flow (used by the CLI and the Streamlit UI):

    draft  ->  approve(draft_id, typed repo name)  ->  submit(approval_id)
                 one-time, short-lived record          re-validate, similar-issue check,
                 bound to the exact draft_hash          at most ONE creation attempt

Rules enforced here:
- An approval is created only by an explicit human action, with the repository
  name typed exactly. Anything else cancels.
- An approval is bound to ``draft_hash``. Editing the draft changes the hash, so
  the old approval no longer matches and approval is required again.
- An approval can be used once, and only before it expires (APPROVAL_TTL_SECONDS).
- Before submitting, the draft is re-validated (secrets, limits), writing must be
  enabled, and the repo must be in GITHUB_WRITE_ALLOWED_REPOS.
- If open issues with a very similar title exist, submission stops and needs a
  second explicit confirmation.
- A draft produces at most one creation attempt (no double submits). POSTs are
  never retried; unknown outcomes are reported as unknown.
- Every approval, cancellation and attempt is written to the audit log.
"""

import difflib
import re
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.actions.audit import AuditLog
from app.actions.issue_draft import IssueDraft, compute_hash, finalize
from app.config import Settings
from app.integrations.github_client import GitHubClient, RepoRef
from app.integrations.github_errors import GitHubError
from app.integrations.github_write_client import CreateIssueResult, GitHubWriteClient, PossibleMatch, resolve_write_repo

SIMILARITY_THRESHOLD = 0.8


class ApprovalError(Exception):
    """An approval or submission was refused. The message is safe to show."""


class Approval(BaseModel):
    approval_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    draft_id: str
    draft_hash: str
    created_at: float
    expires_at: float
    used: bool = False


class SimilarIssue(BaseModel):
    number: int
    title: str
    html_url: str
    similarity: float


class SubmissionResult(BaseModel):
    status: Literal["created", "failed", "unknown", "blocked", "needs_confirmation"]
    draft_id: str
    repo: str
    draft_hash: str
    number: int | None = None
    html_url: str | None = None
    error_category: str | None = None
    message: str = ""
    similar_issues: list[SimilarIssue] = Field(default_factory=list)
    possible_matches: list[PossibleMatch] = Field(default_factory=list)


def _normalize(title: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", title.lower()).split())


class IssueApprovalService:
    """Holds a session's drafts and approvals, and performs approved creations."""

    def __init__(
        self,
        settings: Settings,
        channel: str,
        project_root: Path | None = None,
        write_client_factory: Callable[[], GitHubWriteClient] | None = None,
        read_client_factory: Callable[[], GitHubClient] | None = None,
        audit: AuditLog | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.settings = settings
        self.channel = channel
        self.project_root = project_root
        self._write_client_factory = write_client_factory or (lambda: GitHubWriteClient(settings))
        self._read_client_factory = read_client_factory or (lambda: GitHubClient(settings))
        self.audit = audit or AuditLog(settings.audit_log_path)
        self._clock = clock
        self.drafts: dict[str, IssueDraft] = {}
        self.approvals: dict[str, Approval] = {}
        self.attempted: set[str] = set()  # draft ids that already had their one creation attempt
        self.results: dict[str, SubmissionResult] = {}

    # ---- drafts ------------------------------------------------------------------

    def add_draft(self, draft: IssueDraft) -> IssueDraft:
        self.drafts[draft.draft_id] = finalize(self.settings, draft, self.project_root)
        return self.drafts[draft.draft_id]

    def edit_draft(self, draft_id: str, title: str | None = None, body: str | None = None,
                   labels: list[str] | None = None) -> IssueDraft:
        """Apply a human edit. Re-validates, changes the hash, and voids earlier approvals."""
        draft = self._draft(draft_id)
        if draft_id in self.attempted:
            raise ApprovalError("This draft was already submitted once and can't be edited.")
        if title is not None:
            draft.title = title
        if body is not None:
            draft.body = body
        if labels is not None:
            draft.labels = labels
        finalize(self.settings, draft, self.project_root)
        for approval in self.approvals.values():
            if approval.draft_id == draft_id and approval.draft_hash != draft.draft_hash:
                approval.used = True  # void: it approved different content
        return draft

    def find_similar(self, draft: IssueDraft) -> list[SimilarIssue]:
        """Open issues in the target repo whose titles are very similar to the draft's."""
        target = _normalize(draft.title)
        with self._read_client_factory() as reader:
            issues = reader.list_issues(RepoRef.parse(draft.repo), state="open", limit=100, sort="updated")
        similar = []
        for issue in issues:
            ratio = difflib.SequenceMatcher(None, target, _normalize(issue.title)).ratio()
            if ratio >= SIMILARITY_THRESHOLD:
                similar.append(SimilarIssue(number=issue.number, title=issue.title, html_url=issue.html_url,
                                            similarity=round(ratio, 2)))
        return sorted(similar, key=lambda s: s.similarity, reverse=True)

    # ---- human decisions -------------------------------------------------------------

    def approve(self, draft_id: str, typed_confirmation: str) -> Approval:
        """Record an explicit human approval. The typed text must equal the destination repo exactly."""
        draft = self._draft(draft_id)
        if typed_confirmation.strip() != draft.repo:
            self.cancel(draft_id)
            raise ApprovalError("Cancelled: the confirmation did not match the repository name.")
        if draft_id in self.attempted:
            raise ApprovalError("This draft was already submitted once.")
        finalize(self.settings, draft, self.project_root)
        if not draft.valid:
            raise ApprovalError("The draft has validation errors: " + " ".join(draft.errors))
        try:
            resolve_write_repo(self.settings, draft.repo)
        except GitHubError as exc:
            raise ApprovalError(str(exc)) from None
        now = self._clock()
        approval = Approval(draft_id=draft_id, draft_hash=draft.draft_hash, created_at=now,
                            expires_at=now + self.settings.approval_ttl_seconds)
        self.approvals[approval.approval_id] = approval
        self.audit.record("approved", draft.repo, draft.draft_hash, len(draft.title), self.channel)
        return approval

    def cancel(self, draft_id: str) -> None:
        draft = self._draft(draft_id)
        for approval in self.approvals.values():
            if approval.draft_id == draft_id:
                approval.used = True
        self.audit.record("cancelled", draft.repo, draft.draft_hash, len(draft.title), self.channel)

    def submit(self, approval_id: str, confirm_similar: bool = False) -> SubmissionResult:
        """Create the issue for a valid, unused, unexpired approval whose hash matches the draft."""
        approval = self.approvals.get(approval_id)
        if approval is None:
            raise ApprovalError("No such approval. Approve the draft first.")
        if approval.used:
            raise ApprovalError("This approval was already used or voided. Approve again.")
        if self._clock() > approval.expires_at:
            approval.used = True
            raise ApprovalError("This approval expired. Review the draft and approve again.")
        draft = self._draft(approval.draft_id)
        if compute_hash(draft.repo, draft.title, draft.body, draft.labels) != approval.draft_hash:
            approval.used = True
            raise ApprovalError("The draft changed after it was approved. Review it and approve again.")
        if draft.draft_id in self.attempted:
            raise ApprovalError("This draft already had its creation attempt (no double submission).")

        # Re-validate right before writing.
        finalize(self.settings, draft, self.project_root)
        if not draft.valid or draft.draft_hash != approval.draft_hash:
            approval.used = True
            self.audit.record("blocked", draft.repo, draft.draft_hash, len(draft.title), self.channel,
                              error_category="validation")
            return SubmissionResult(status="blocked", draft_id=draft.draft_id, repo=draft.repo,
                                    draft_hash=draft.draft_hash, error_category="validation",
                                    message="Blocked: " + " ".join(draft.errors or ["the draft changed"]))
        try:
            resolve_write_repo(self.settings, draft.repo)
        except GitHubError as exc:
            self.audit.record("blocked", draft.repo, draft.draft_hash, len(draft.title), self.channel,
                              error_category="not_writable")
            return SubmissionResult(status="blocked", draft_id=draft.draft_id, repo=draft.repo,
                                    draft_hash=draft.draft_hash, error_category="not_writable", message=str(exc))

        if not confirm_similar:
            try:
                similar = self.find_similar(draft)
            except GitHubError as exc:
                return SubmissionResult(status="needs_confirmation", draft_id=draft.draft_id, repo=draft.repo,
                                        draft_hash=draft.draft_hash, error_category="similarity_check_failed",
                                        message=f"Could not check for similar issues ({exc}). Confirm to continue.")
            if similar:
                return SubmissionResult(status="needs_confirmation", draft_id=draft.draft_id, repo=draft.repo,
                                        draft_hash=draft.draft_hash, similar_issues=similar,
                                        message="Similar open issues exist. Confirm again to create anyway.")

        # Point of no return: consume the approval and the draft's single attempt BEFORE the request.
        approval.used = True
        self.attempted.add(draft.draft_id)
        client = self._write_client_factory()
        try:
            outcome: CreateIssueResult = client.create_issue(draft.repo, draft.title, draft.body, draft.labels)
        except GitHubError as exc:
            outcome = CreateIssueResult(status="failed", repo=draft.repo, error_category="not_writable",
                                        message=str(exc))
        finally:
            client.close()
        self.audit.record(outcome.status, draft.repo, draft.draft_hash, len(draft.title), self.channel,
                          issue_number=outcome.number, issue_url=outcome.html_url,
                          error_category=outcome.error_category)
        result = SubmissionResult(status=outcome.status, draft_id=draft.draft_id, repo=draft.repo,
                                  draft_hash=draft.draft_hash, number=outcome.number, html_url=outcome.html_url,
                                  error_category=outcome.error_category, message=outcome.message,
                                  possible_matches=outcome.possible_matches)
        self.results[draft.draft_id] = result
        return result

    def _draft(self, draft_id: str) -> IssueDraft:
        if draft_id not in self.drafts:
            raise ApprovalError("Unknown draft.")
        return self.drafts[draft_id]
