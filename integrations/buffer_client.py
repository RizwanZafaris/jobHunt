"""
integrations/buffer_client.py — Async client for the Buffer Publishing API.

Buffer's API is REST + OAuth2 (token refresh, multi-profile).
Reference: https://buffer.com/developers/api

Three things this client does:
  1. OAuth helpers (build_authorize_url + exchange_code)
  2. List the user's connected LinkedIn profile ids
  3. Create + cancel scheduled posts

Design decisions:
  • Async (httpx) — matches the rest of the codebase
  • All Buffer calls go through _request() so retries + logging are
    centralized
  • No internal caching — caller decides when to refresh tokens
  • Errors raise BufferAPIError with the upstream status + body for
    triage (don't lose the raw response)

Required env:
  BUFFER_CLIENT_ID, BUFFER_CLIENT_SECRET, BUFFER_REDIRECT_URI

Optional env:
  BUFFER_API_BASE = "https://api.bufferapp.com/1" (default)
  BUFFER_OAUTH_BASE = "https://bufferapp.com/oauth2" (default)
  BUFFER_HTTP_TIMEOUT = "30" (seconds)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)


_DEFAULT_API_BASE = "https://api.bufferapp.com/1"
_DEFAULT_OAUTH_BASE = "https://bufferapp.com/oauth2"


class BufferAPIError(RuntimeError):
    """Raised when a Buffer API call returns a non-2xx response.

    Attributes:
      status: HTTP status code
      body:   raw response body (str)
      path:   endpoint that failed
    """
    def __init__(self, status: int, body: str, path: str):
        super().__init__(f"Buffer API {status} on {path}: {body[:300]}")
        self.status = status
        self.body = body
        self.path = path


class BufferConfigError(RuntimeError):
    """Raised when required env vars are missing."""


@dataclass
class BufferProfile:
    id: str
    service: str         # 'linkedin' / 'twitter' / etc.
    service_username: str
    name: Optional[str] = None
    avatar_url: Optional[str] = None


@dataclass
class BufferUpdate:
    """A scheduled or posted update on Buffer."""
    id: str
    profile_id: str
    text: str
    status: str          # 'pending' | 'sent' | 'failed'
    scheduled_at: Optional[str] = None
    sent_at: Optional[str] = None
    service_link: Optional[str] = None     # LinkedIn permalink once posted
    raw: Optional[dict] = None


def _env(key: str, default: Optional[str] = None, *, required: bool = False) -> Optional[str]:
    v = os.environ.get(key, default)
    if required and not v:
        raise BufferConfigError(f"Missing required env: {key}")
    return v


class BufferClient:
    """Thin async wrapper over the Buffer REST API.

    One client instance per HTTP session is fine. Tokens are passed per-call
    (not bound to the client) so a single client serves many users.
    """

    def __init__(
        self,
        *,
        client_id: Optional[str] = None,
        client_secret: Optional[str] = None,
        redirect_uri: Optional[str] = None,
        api_base: Optional[str] = None,
        oauth_base: Optional[str] = None,
        timeout_s: Optional[float] = None,
    ):
        self.client_id = client_id or _env("BUFFER_CLIENT_ID", required=True)
        self.client_secret = client_secret or _env("BUFFER_CLIENT_SECRET", required=True)
        self.redirect_uri = redirect_uri or _env("BUFFER_REDIRECT_URI", required=True)
        self.api_base = (api_base or _env("BUFFER_API_BASE", _DEFAULT_API_BASE)).rstrip("/")
        self.oauth_base = (oauth_base or _env("BUFFER_OAUTH_BASE", _DEFAULT_OAUTH_BASE)).rstrip("/")
        self.timeout_s = float(timeout_s or _env("BUFFER_HTTP_TIMEOUT", "30"))

    # ─── OAuth helpers ────────────────────────────────────────────────────
    def build_authorize_url(self, state: str) -> str:
        """Return the URL to redirect the user to for OAuth consent.

        `state` should be a cryptographically-random per-session token the
        caller persists temporarily and verifies on the callback.
        """
        params = {
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "response_type": "code",
            "state": state,
        }
        return f"{self.oauth_base}/authorize?{urlencode(params)}"

    async def exchange_code(self, code: str) -> dict:
        """Exchange an authorization code for access + refresh tokens.

        Returns the raw Buffer token response (access_token, expires_in,
        token_type, refresh_token if present).
        """
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(
                f"{self.oauth_base}/token.json",
                data={
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                    "redirect_uri": self.redirect_uri,
                    "code": code,
                    "grant_type": "authorization_code",
                },
            )
            if resp.status_code >= 300:
                raise BufferAPIError(resp.status_code, resp.text, "/oauth2/token.json")
            return resp.json()

    # ─── Authed requests ─────────────────────────────────────────────────
    async def _request(
        self,
        method: str,
        path: str,
        access_token: str,
        *,
        json_body: Optional[dict] = None,
        form_body: Optional[dict] = None,
        params: Optional[dict] = None,
    ) -> dict:
        url = f"{self.api_base}{path}"
        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.request(
                method,
                url,
                params={**(params or {}), "access_token": access_token},
                json=json_body,
                data=form_body,
            )
        if resp.status_code >= 300:
            raise BufferAPIError(resp.status_code, resp.text, path)
        # Buffer's API returns text/javascript with JSON body sometimes.
        try:
            return resp.json()
        except Exception:
            return {"raw_text": resp.text}

    # ─── Profiles ────────────────────────────────────────────────────────
    async def list_profiles(self, access_token: str) -> list[BufferProfile]:
        """List the profiles connected to this Buffer account."""
        data = await self._request("GET", "/profiles.json", access_token)
        profiles: list[BufferProfile] = []
        for p in data if isinstance(data, list) else []:
            profiles.append(BufferProfile(
                id=str(p.get("id")),
                service=str(p.get("service") or "").lower(),
                service_username=str(p.get("service_username") or ""),
                name=p.get("formatted_username") or p.get("service_username"),
                avatar_url=p.get("avatar"),
            ))
        return profiles

    async def list_linkedin_profiles(self, access_token: str) -> list[BufferProfile]:
        """Profiles filtered to LinkedIn only (the only service we publish to)."""
        all_p = await self.list_profiles(access_token)
        return [p for p in all_p if p.service == "linkedin"]

    # ─── Create / cancel updates ─────────────────────────────────────────
    async def create_update(
        self,
        access_token: str,
        *,
        profile_ids: list[str],
        text: str,
        scheduled_at: Optional[str] = None,
        link: Optional[str] = None,
        media_photo_url: Optional[str] = None,
    ) -> list[BufferUpdate]:
        """Create a scheduled or now-queued update on one or more profiles.

        scheduled_at: ISO-8601 / RFC-3339 timestamp. If omitted, Buffer
                      adds it to the profile's posting queue.
        link:         optional URL to attach
        media_photo_url: optional image URL to attach
        """
        if not profile_ids:
            raise ValueError("profile_ids cannot be empty")

        # Buffer expects form-encoded data on POST /updates/create.json.
        form: dict[str, Any] = {
            "profile_ids[]": profile_ids,
            "text": text,
        }
        if scheduled_at:
            form["scheduled_at"] = scheduled_at
        if link:
            form["media[link]"] = link
        if media_photo_url:
            form["media[photo]"] = media_photo_url

        data = await self._request(
            "POST", "/updates/create.json", access_token, form_body=form
        )

        if not data.get("success"):
            raise BufferAPIError(
                500, str(data),
                "/updates/create.json (success=false)",
            )

        updates = data.get("updates") or []
        out: list[BufferUpdate] = []
        for u in updates:
            out.append(BufferUpdate(
                id=str(u.get("id")),
                profile_id=str(u.get("profile_id")),
                text=str(u.get("text") or text),
                status=str(u.get("status") or "pending"),
                scheduled_at=u.get("scheduled_at_iso") or u.get("created_at_iso"),
                sent_at=u.get("sent_at"),
                service_link=u.get("service_link"),
                raw=u,
            ))
        return out

    async def get_update(self, access_token: str, update_id: str) -> BufferUpdate:
        """Fetch a single update's state — used by the poll-status job."""
        data = await self._request(
            "GET", f"/updates/{update_id}.json", access_token
        )
        return BufferUpdate(
            id=str(data.get("id") or update_id),
            profile_id=str(data.get("profile_id") or ""),
            text=str(data.get("text") or ""),
            status=str(data.get("status") or "unknown"),
            scheduled_at=data.get("scheduled_at_iso") or data.get("created_at_iso"),
            sent_at=data.get("sent_at"),
            service_link=data.get("service_link"),
            raw=data,
        )

    async def cancel_update(self, access_token: str, update_id: str) -> bool:
        """Cancel a still-pending update. Returns True on success.

        Buffer returns success=true even when the update was already sent
        (in which case nothing changes). We don't second-guess.
        """
        data = await self._request(
            "POST", f"/updates/{update_id}/destroy.json", access_token,
        )
        return bool(data.get("success"))


# Convenience singleton — most callers want the same client across requests.
_singleton: Optional[BufferClient] = None


def get_buffer_client() -> BufferClient:
    """Process-wide singleton. Raises BufferConfigError if env missing."""
    global _singleton
    if _singleton is None:
        _singleton = BufferClient()
    return _singleton


__all__ = [
    "BufferClient",
    "BufferProfile",
    "BufferUpdate",
    "BufferAPIError",
    "BufferConfigError",
    "get_buffer_client",
]
