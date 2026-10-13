"""GitHub client errors. Messages are written by us: never a token or raw headers."""

from datetime import datetime


class GitHubError(Exception):
    """Base class for all GitHub integration errors."""


class GitHubNotConfiguredError(GitHubError):
    """No repository is configured or allowed."""


class RepoNotAllowedError(GitHubError):
    """The repository is not in GITHUB_ALLOWED_REPOS."""


class InvalidRepoError(GitHubError):
    """The repository identifier is not a valid owner/name."""


class NotFoundError(GitHubError):
    """404: the resource doesn't exist, or it's private and the token can't see it."""


class AuthError(GitHubError):
    """401: the token is invalid or expired."""


class ForbiddenError(GitHubError):
    """403 that isn't a rate limit: the token lacks permission."""


class ValidationError(GitHubError):
    """422: GitHub rejected the request parameters (e.g. an invalid search query)."""


class GitHubUnavailableError(GitHubError):
    """Timeouts, connection failures and 5xx responses (after one retry)."""


class RateLimitError(GitHubError):
    """403/429 caused by a primary or secondary rate limit. Never retried automatically."""

    def __init__(self, message: str, reset_at: datetime | None = None, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.reset_at = reset_at
        self.retry_after_seconds = retry_after_seconds
