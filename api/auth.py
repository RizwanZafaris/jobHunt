"""
JWT verification for Supabase-issued tokens.

Supabase signs JWTs with HS256 against the project's JWT secret
(`SUPABASE_JWT_SECRET`). Audience is the literal string `"authenticated"`
for end-user sessions. We only need to verify; we never sign.

Usage:
    claims = verify_supabase_jwt(token)
    user_id = claims["sub"]    # UUID string
    email   = claims["email"]  # str

Failure modes (all map to HTTP 401 except missing secret which is 500):
    - missing/invalid signature   → 401 invalid_token
    - expired                     → 401 token_expired
    - SUPABASE_JWT_SECRET unset   → 500 auth_misconfigured

A small in-process LRU cache is available behind two flags
(RIZWAN_SINGLE_USER_MODE=0 AND JWT_VERIFY_CACHE=1) so dev tooling that
fires many requests per second with the same token doesn't pay the
HMAC cost on every call. It is OFF by default everywhere else: in
single-user mode this code path is bypassed entirely, and in
production we want every request to re-check expiry against the wall
clock.
"""

from __future__ import annotations

import hashlib
import os
from functools import lru_cache
from typing import Any

from fastapi import HTTPException
from jose import jwt
from jose.exceptions import ExpiredSignatureError, JWTError


_SUPABASE_AUDIENCE = "authenticated"
_ALGORITHM = "HS256"


def _get_jwt_secret() -> str:
    secret = os.getenv("SUPABASE_JWT_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="auth_misconfigured")
    return secret


def _decode(token: str, secret: str) -> dict[str, Any]:
    """Raw decode — kept tiny so the LRU wrapper has a clean target."""
    return jwt.decode(
        token,
        secret,
        algorithms=[_ALGORITHM],
        audience=_SUPABASE_AUDIENCE,
        # Supabase doesn't set `iss` consistently across versions; skip
        # the issuer check rather than maintain a project-id list here.
        options={"verify_iss": False},
    )


# Cache key is (sha256(token)[:32], minute-bucket of exp). We never put
# the token itself in the key — the digest avoids leaking secrets into
# Python's introspection surface (locals(), tracebacks). The bucket
# means a cached entry expires naturally as the token approaches its
# stated `exp`. Size 1024 is enough for a single-process dev box; in
# production the cache is disabled so this is irrelevant there.
@lru_cache(maxsize=1024)
def _cached_decode(token_digest: str, exp_bucket: int, secret: str, raw_token: str) -> dict[str, Any]:
    return _decode(raw_token, secret)


def _cache_enabled() -> bool:
    return (
        os.getenv("RIZWAN_SINGLE_USER_MODE", "1") == "0"
        and os.getenv("JWT_VERIFY_CACHE", "0") == "1"
    )


def verify_supabase_jwt(token: str) -> dict[str, Any]:
    """
    Verify a Supabase HS256 JWT and return its claims.

    Raises HTTPException(401) on any verification failure and
    HTTPException(500) if the server is missing SUPABASE_JWT_SECRET.
    """
    if not token:
        raise HTTPException(status_code=401, detail="invalid_token")

    secret = _get_jwt_secret()

    try:
        if _cache_enabled():
            digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]
            # Peek at exp without verifying signature so we can bucket; if the
            # token is malformed this will throw and we'll fall through to the
            # JWTError handler below.
            unverified = jwt.get_unverified_claims(token)
            exp = int(unverified.get("exp", 0))
            # 60s buckets — close enough that two requests within the same
            # minute can share a verification result.
            exp_bucket = exp // 60
            return _cached_decode(digest, exp_bucket, secret, token)
        return _decode(token, secret)
    except ExpiredSignatureError as exc:
        raise HTTPException(status_code=401, detail="token_expired") from exc
    except JWTError as exc:
        raise HTTPException(status_code=401, detail="invalid_token") from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — defensive: any decode-time bug → 401
        raise HTTPException(status_code=401, detail="invalid_token") from exc
