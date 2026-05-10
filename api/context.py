"""
FastAPI dependency for the current authenticated user.

The contract: every per-tenant endpoint adds
`user: User = Depends(get_current_user)` and filters its DB queries by
`user.id`. Two modes exist:

1. **Single-user mode** (RIZWAN_SINGLE_USER_MODE=1, the default).
   Bypasses JWT entirely and returns Rizwan's seeded row. Auto-provisions
   on first cold start so the seed migration is not strictly required.
   This keeps self-use working through Sprint 1 while we wire endpoints.

2. **Multi-tenant mode** (RIZWAN_SINGLE_USER_MODE=0).
   Parses `Authorization: Bearer <token>`, verifies via
   `auth.verify_supabase_jwt`, and looks up (or auto-provisions) the
   user keyed by claims['sub']. Returns 401 on any failure.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException

from api.auth import verify_supabase_jwt
from api.users import (
    RIZWAN_EMAIL,
    RIZWAN_FULL_NAME,
    RIZWAN_USER_ID,
    User,
    create_user,
    get_user_by_id,
)


def _is_single_user_mode() -> bool:
    return os.getenv("RIZWAN_SINGLE_USER_MODE", "1") == "1"


def _ensure_rizwan() -> User:
    """Return Rizwan's row, auto-provisioning if the seed migration hasn't run."""
    user = get_user_by_id(RIZWAN_USER_ID)
    if user is not None:
        return user
    return create_user(
        email=RIZWAN_EMAIL,
        full_name=RIZWAN_FULL_NAME,
        id=RIZWAN_USER_ID,
        plan="lifetime",
        is_admin=True,
    )


def _extract_full_name(claims: dict[str, Any]) -> str | None:
    """Pull a display name out of Supabase's user_metadata if present."""
    metadata = claims.get("user_metadata") or {}
    if isinstance(metadata, dict):
        name = metadata.get("full_name") or metadata.get("name")
        if isinstance(name, str) and name.strip():
            return name.strip()
    return None


async def get_current_user(
    authorization: str | None = Header(default=None),
) -> User:
    """
    Resolve the authenticated user for this request.

    Returns the Rizwan row in single-user mode, regardless of whether
    a header was sent. Otherwise verifies the bearer token and
    auto-provisions on first hit.
    """
    if _is_single_user_mode():
        return _ensure_rizwan()

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="invalid_token")
    token = authorization.split(" ", 1)[1].strip()

    claims = verify_supabase_jwt(token)
    sub = claims.get("sub")
    if not isinstance(sub, str):
        raise HTTPException(status_code=401, detail="invalid_token")
    try:
        user_id = UUID(sub)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=401, detail="invalid_token") from exc

    existing = get_user_by_id(user_id)
    if existing is not None:
        return existing

    # First sign-in for this Supabase user — provision a row.
    email = claims.get("email")
    if not isinstance(email, str) or "@" not in email:
        # Token verified but doesn't carry an email; we can't create a
        # row that satisfies the NOT NULL UNIQUE constraint.
        raise HTTPException(status_code=401, detail="invalid_token")

    return create_user(
        email=email,
        full_name=_extract_full_name(claims),
        id=user_id,
    )


def get_current_user_id(user: User = Depends(get_current_user)) -> UUID:
    """Convenience: return just the UUID for endpoints that don't need the row."""
    return user.id


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate admin-only endpoints. Raises 403 for non-admins."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin_required")
    return user
