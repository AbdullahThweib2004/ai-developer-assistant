"""User endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.auth import USERS, get_current_user, require_role

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me")
def read_me(user: dict = Depends(get_current_user)) -> dict:
    return user


@router.get("/{user_id}")
def read_user(user_id: str, _admin: dict = Depends(require_role("admin"))) -> dict:
    user = USERS.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    return user
