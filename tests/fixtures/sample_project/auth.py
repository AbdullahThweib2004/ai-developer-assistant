"""User authentication helpers."""

import hashlib
import hmac
import secrets

PBKDF2_ITERATIONS = 200_000
SESSION_TOKEN_BYTES = 32


def hash_password(password: str, salt: bytes) -> bytes:
    """Derive a password hash with PBKDF2-HMAC-SHA256."""
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ITERATIONS)


def verify_user_credentials(username: str, password: str, user_store: dict) -> bool:
    """Check a username and password against the stored salted hash.

    Uses a constant-time comparison to avoid timing attacks. Unknown users
    return False instead of raising, so callers can't tell which part failed.
    """
    record = user_store.get(username)
    if record is None:
        return False
    candidate = hash_password(password, record["salt"])
    return hmac.compare_digest(candidate, record["password_hash"])


def create_session_token() -> str:
    """Create a random, URL-safe session token after a successful login."""
    return secrets.token_urlsafe(SESSION_TOKEN_BYTES)
