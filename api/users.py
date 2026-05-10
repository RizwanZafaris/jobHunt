"""
User table accessors.

The `users` table is created by db/migrations/2026_05_10_001_multi_tenancy.sql
and seeded with Rizwan's row (id=00000000-0000-0000-0000-000000000001) so
single-user mode keeps working unchanged.

All functions here use the service-role Supabase client from
db.client.get_supabase(), which bypasses RLS. That is correct for now:
authentication and tenancy enforcement happen in `api/context.py` (we
filter by user_id in application code). Once we move to user-scoped
JWT clients, these helpers will need updating — see api/AUTH.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from db.client import get_supabase


# Stable id for Rizwan's seeded row. Hard-coded everywhere because it
# also appears in the migration's backfill default.
RIZWAN_USER_ID: UUID = UUID("00000000-0000-0000-0000-000000000001")
RIZWAN_EMAIL: str = "rizwanzaffar.pk@gmail.com"
RIZWAN_FULL_NAME: str = "Rizwan Zafar"


class User(BaseModel):
    id: UUID
    email: EmailStr
    full_name: str | None = None
    plan: str = Field(default="free")
    is_admin: bool = False
    created_at: datetime

    # Allow Supabase rows that include extra columns (linkedin_url, …)
    # without listing them all here.
    model_config = {"extra": "ignore"}


def _row_to_user(row: dict[str, Any]) -> User:
    """Coerce a Supabase row into our typed User model."""
    return User.model_validate(row)


def get_user_by_id(user_id: UUID) -> User | None:
    """Fetch a user by primary key. Returns None if not found."""
    db = get_supabase()
    result = (
        db.table("users")
        .select("*")
        .eq("id", str(user_id))
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return _row_to_user(rows[0]) if rows else None


def get_user_by_email(email: str) -> User | None:
    """Fetch a user by email. Returns None if not found."""
    db = get_supabase()
    result = (
        db.table("users")
        .select("*")
        .eq("email", email)
        .limit(1)
        .execute()
    )
    rows = result.data or []
    return _row_to_user(rows[0]) if rows else None


def create_user(
    email: str,
    full_name: str | None = None,
    **extras: Any,
) -> User:
    """
    Insert a new user row. Used for first-sign-in auto-provisioning.

    `**extras` lets callers pass `id` (to bind to a Supabase-issued
    UUID), `plan`, `is_admin`, `linkedin_url`, etc. without bloating
    the signature for the common case.
    """
    payload: dict[str, Any] = {"email": email}
    if full_name is not None:
        payload["full_name"] = full_name
    for key, value in extras.items():
        if value is None:
            continue
        # UUIDs need str-ification for postgrest.
        if isinstance(value, UUID):
            payload[key] = str(value)
        else:
            payload[key] = value

    db = get_supabase()
    # `upsert(on_conflict="email")` is concurrency-safe: two parallel
    # first-time logins for the same address resolve to one row.
    result = (
        db.table("users")
        .upsert(payload, on_conflict="email")
        .execute()
    )
    rows = result.data or []
    if not rows:
        # Fall back to a fresh read — some postgrest versions don't
        # echo the row on upsert.
        existing = get_user_by_email(email)
        if existing is None:
            raise RuntimeError(f"create_user failed silently for {email}")
        return existing
    return _row_to_user(rows[0])


def update_user_plan(user_id: UUID, plan: str) -> User:
    """Update the user's billing plan. Returns the fresh row."""
    db = get_supabase()
    result = (
        db.table("users")
        .update({"plan": plan})
        .eq("id", str(user_id))
        .execute()
    )
    rows = result.data or []
    if not rows:
        # update() returns no rows when nothing matched.
        raise ValueError(f"user {user_id} not found")
    return _row_to_user(rows[0])
