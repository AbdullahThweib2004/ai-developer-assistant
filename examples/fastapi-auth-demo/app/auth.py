"""Bearer token authentication: header parsing, JWT verification, current user."""

import time

import jwt
from fastapi import Depends, Header, HTTPException, status

from app.config import settings

USERS = {
    "u-100": {"id": "u-100", "name": "Ada", "roles": ["user", "admin"]},
    "u-200": {"id": "u-200", "name": "Linus", "roles": ["user"]},
}


class AuthError(Exception):
    """Raised for any authentication failure; mapped to 401."""


def parse_authorization_header(value: str | None) -> str:
    """Return the token from an "Authorization: <scheme> <token>" header."""
    if not value:
        raise AuthError("Missing Authorization header")
    scheme, _, token = value.strip().partition(" ")
    if scheme != "bearer" or not token:
        raise AuthError("Invalid authentication scheme")
    return token.strip()


def verify_token(token: str) -> dict:
    """Check the signature, expiry and age of an access token and return its claims."""
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError as exc:
        raise AuthError("Token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthError("Invalid token") from exc
    issued_at = claims.get("iat", 0)
    if time.time() - issued_at > settings.access_token_max_age_minutes * 60:
        raise AuthError("Token too old")
    return claims


def get_current_user(authorization: str | None = Header(default=None)) -> dict:
    """FastAPI dependency: the user for the request's bearer token, or 401."""
    try:
        claims = verify_token(parse_authorization_header(authorization))
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    user = USERS.get(claims.get("sub", ""))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unknown user",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


def require_role(role: str):
    """FastAPI dependency factory: 403 unless the current user has ``role``."""

    def checker(user: dict = Depends(get_current_user)) -> dict:
        if role not in user["roles"]:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return checker
