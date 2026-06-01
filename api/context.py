"""
FastAPI dependency for the current authenticated user.

The contract: every per-tenant endpoint adds
`user: User = Depends(get_current_user)` and filters its DB queries by
`user.id`. Three resolution paths exist:

1. **Single-user mode** (RIZWAN_SINGLE_USER_MODE=1, the default).
   Bypasses JWT entirely and returns Rizwan's seeded row. Auto-provisions
   on first cold start so the seed migration is not strictly required.
   This keeps self-use working through Sprint 1 while we wire endpoints.

2. **Service-secret path** (multi-tenant mode + valid X-Secret-Key, no
   bearer token). For the worker / cron / internal callers that act as the
   *system* and have no user session: they resolve to the seed/system user
   without a tenant guess, so cron-driven writes still land somewhere
   sensible. End-user requests should never hit this branch — they carry a
   bearer JWT, which takes precedence.

3. **Multi-tenant JWT mode** (RIZWAN_SINGLE_USER_MODE=0).
   Parses `Authorization: Bearer <token>`, verifies via
   `auth.verify_supabase_jwt`, and looks up (or auto-provisions) the
   user keyed by claims['sub']. Returns 401 on any failure.

Admin determination (Phase 1, P1-1): a user is admin if their DB row has
`is_admin=true` **or** their id is in the `ADMIN_USER_IDS` env allowlist
(comma-separated UUIDs, defaulting to the seed user_001 UUID — "the owner is
admin"). The env overlay means the owner is admin even if a migration/backfill
left the column false, and lets ops grant admin without a DB write. Every
resolved user has its `is_admin` reconciled against this rule before return.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException

from agents.run_context import set_current_user_id
from api.auth import check_service_secret, verify_supabase_jwt
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


def _admin_user_ids() -> set[str]:
    """The admin allowlist: ADMIN_USER_IDS env (CSV) ∪ {seed user_001}.

    THE OWNER IS ADMIN: the seed user (Rizwan, user_001) is always in the set,
    so single-user mode and the owner's own multi-tenant session are admin
    without any extra config. Additional admins can be added via the
    comma-separated ``ADMIN_USER_IDS`` env var. UUIDs are normalised to their
    canonical lowercase string form so comparison is case/format insensitive.
    """
    ids: set[str] = {str(RIZWAN_USER_ID)}
    raw = os.getenv("ADMIN_USER_IDS", "")
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            ids.add(str(UUID(piece)))
        except (ValueError, TypeError):
            # Ignore malformed entries rather than failing every request.
            continue
    return ids


def _is_admin_user(user: User) -> bool:
    """True if the user's DB row says admin OR their id is in the allowlist."""
    if user.is_admin:
        return True
    return str(user.id) in _admin_user_ids()


def _reconcile_admin(user: User) -> User:
    """Return ``user`` with ``is_admin`` overlaid from the env allowlist.

    Never demotes a DB-admin; only promotes ids present in ``ADMIN_USER_IDS``
    (or the seed user). Returns the same object when nothing changes so the
    common path allocates nothing.
    """
    resolved = _is_admin_user(user)
    if resolved == user.is_admin:
        return user
    return user.model_copy(update={"is_admin": resolved})


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
    x_secret_key: str | None = Header(default=None),
) -> User:
    """
    Resolve the authenticated user for this request.

    Resolution order:
      1. Single-user mode → the Rizwan row (header-agnostic), as before.
      2. A valid bearer JWT → the token's user (verify + auto-provision).
      3. No bearer token but a valid service ``X-Secret-Key`` → the system
         (seed) user, so worker/cron/internal callers resolve a tenant without
         guessing one.
      4. Otherwise → 401.

    Every resolved user's ``is_admin`` is reconciled against the env allowlist,
    and the resolved id is published to the per-request tenant contextvar
    (``agents.run_context``) for downstream (Phase 2) consumers.
    """
    user = await _resolve_user(authorization, x_secret_key)
    user = _reconcile_admin(user)
    # Publish the tenant id for the rest of this request/task. Set unconditionally
    # (including single-user mode) so downstream readers see a consistent value.
    set_current_user_id(str(user.id))
    return user


async def _resolve_user(
    authorization: str | None,
    x_secret_key: str | None,
) -> User:
    """Pick the right resolution path (see get_current_user docstring)."""
    if _is_single_user_mode():
        return _ensure_rizwan()

    # Bearer JWT takes precedence over the service secret: an end-user request
    # should always be attributed to that end user even if it also (wrongly)
    # carries a service key.
    has_bearer = bool(authorization and authorization.lower().startswith("bearer "))

    if not has_bearer:
        # No user token. Allow the system/service path iff a valid service
        # secret is presented — this is the worker/cron/internal branch.
        if check_service_secret(x_secret_key):
            return _ensure_rizwan()
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


def get_access_token(
    authorization: str | None = Header(default=None),
) -> str | None:
    """Extract the raw bearer access token from the Authorization header.

    Returns None when there's no bearer token (single-user / service-secret
    callers). Does NOT verify the token — that's ``get_current_user``'s job;
    this is only the raw value to hand to ``db.client.get_user_supabase`` so the
    per-request RLS client can apply it.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    return authorization.split(" ", 1)[1].strip() or None


def get_request_supabase(
    access_token: str | None = Depends(get_access_token),
):
    """FastAPI dependency: the Supabase client to use for THIS request.

    In multi-tenant mode with a bearer token, returns an anon-key client with
    the user's JWT applied (RLS-enforced). In single-user mode, or for
    service/cron callers without a token, returns the service-role singleton —
    so today's behavior is unchanged. See ``db.client.get_user_supabase``.

    Reference usage (NOT yet applied across endpoints — that's finding 2)::

        @app.get("/jobs")
        async def list_jobs(
            user: User = Depends(get_current_user),
            db = Depends(get_request_supabase),
        ):
            return db.table("jobs").select("*").eq("user_id", str(user.id)).execute().data
    """
    # Local import to avoid any import-time cycle between api.context and db.client.
    from db.client import get_user_supabase

    return get_user_supabase(access_token)


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Gate admin-only endpoints. Raises 403 for non-admins.

    ``user`` already has its ``is_admin`` reconciled against the env allowlist
    by ``get_current_user``, so the seed owner (user_001) and any id in
    ``ADMIN_USER_IDS`` pass here without a DB write.
    """
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin_required")
    return user
