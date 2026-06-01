"""
tests/test_buffer_client.py — unit tests for the Buffer HTTP client.

Covers: env validation, OAuth URL builder, request error mapping,
profile-list parsing, update-creation request shape.

No real Buffer API calls — httpx.AsyncClient is patched.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from integrations.buffer_client import (
    BufferAPIError,
    BufferClient,
    BufferConfigError,
    BufferUpdate,
)


def _client():
    return BufferClient(
        client_id="cid",
        client_secret="csec",
        redirect_uri="https://example.test/cb",
    )


class TestConfig:
    def test_missing_env_raises(self, monkeypatch):
        monkeypatch.delenv("BUFFER_CLIENT_ID", raising=False)
        monkeypatch.delenv("BUFFER_CLIENT_SECRET", raising=False)
        monkeypatch.delenv("BUFFER_REDIRECT_URI", raising=False)
        with pytest.raises(BufferConfigError):
            BufferClient()


class TestOAuthURL:
    def test_authorize_url_includes_state(self):
        c = _client()
        url = c.build_authorize_url(state="random-state-123")
        assert "state=random-state-123" in url
        assert "client_id=cid" in url
        assert "redirect_uri=https" in url
        assert "response_type=code" in url


def _mock_response(*, status: int = 200, json_body=None, text: str = ""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text or ""
    if json_body is not None:
        resp.json = MagicMock(return_value=json_body)
    else:
        resp.json = MagicMock(side_effect=ValueError("no json"))
    return resp


class TestExchangeCode:
    @pytest.mark.asyncio
    async def test_exchange_code_returns_tokens(self):
        c = _client()
        expected = {"access_token": "AT", "refresh_token": "RT", "expires_in": 3600}

        mock_client_cls = MagicMock()
        mock_client_inst = MagicMock()
        mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
        mock_client_inst.__aexit__ = AsyncMock(return_value=None)
        mock_client_inst.post = AsyncMock(return_value=_mock_response(json_body=expected))
        mock_client_cls.return_value = mock_client_inst

        with patch("integrations.buffer_client.httpx.AsyncClient", mock_client_cls):
            result = await c.exchange_code("the-code")
        assert result == expected

    @pytest.mark.asyncio
    async def test_exchange_code_400_raises(self):
        c = _client()
        mock_client_inst = MagicMock()
        mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
        mock_client_inst.__aexit__ = AsyncMock(return_value=None)
        mock_client_inst.post = AsyncMock(
            return_value=_mock_response(status=400, text="bad code"),
        )
        with patch(
            "integrations.buffer_client.httpx.AsyncClient",
            MagicMock(return_value=mock_client_inst),
        ):
            with pytest.raises(BufferAPIError) as exc:
                await c.exchange_code("x")
            assert exc.value.status == 400
            assert "bad code" in exc.value.body


class TestRequestErrors:
    @pytest.mark.asyncio
    async def test_non_2xx_raises_api_error(self):
        c = _client()
        mock_client_inst = MagicMock()
        mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
        mock_client_inst.__aexit__ = AsyncMock(return_value=None)
        mock_client_inst.request = AsyncMock(
            return_value=_mock_response(status=429, text="rate limit"),
        )
        with patch(
            "integrations.buffer_client.httpx.AsyncClient",
            MagicMock(return_value=mock_client_inst),
        ):
            with pytest.raises(BufferAPIError) as exc:
                await c._request("GET", "/profiles.json", "AT")
            assert exc.value.status == 429


class TestListProfiles:
    @pytest.mark.asyncio
    async def test_filters_linkedin_only(self):
        c = _client()
        profiles_json = [
            {"id": "p1", "service": "twitter",  "service_username": "rz_twitter"},
            {"id": "p2", "service": "linkedin", "service_username": "rizwan-linkedin",
             "formatted_username": "Rizwan Zafar"},
            {"id": "p3", "service": "facebook", "service_username": "rz_fb"},
        ]
        mock_client_inst = MagicMock()
        mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
        mock_client_inst.__aexit__ = AsyncMock(return_value=None)
        mock_client_inst.request = AsyncMock(
            return_value=_mock_response(json_body=profiles_json),
        )
        with patch(
            "integrations.buffer_client.httpx.AsyncClient",
            MagicMock(return_value=mock_client_inst),
        ):
            li = await c.list_linkedin_profiles("AT")
        assert len(li) == 1
        assert li[0].id == "p2"
        assert li[0].service == "linkedin"
        assert li[0].name == "Rizwan Zafar"


class TestCreateUpdate:
    @pytest.mark.asyncio
    async def test_success_returns_buffer_update_list(self):
        c = _client()
        response_json = {
            "success": True,
            "updates": [
                {
                    "id": "buf-update-1",
                    "profile_id": "p2",
                    "text": "hello world",
                    "status": "pending",
                    "scheduled_at_iso": "2026-05-27T12:00:00Z",
                },
            ],
        }
        mock_client_inst = MagicMock()
        mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
        mock_client_inst.__aexit__ = AsyncMock(return_value=None)
        mock_client_inst.request = AsyncMock(
            return_value=_mock_response(json_body=response_json),
        )
        with patch(
            "integrations.buffer_client.httpx.AsyncClient",
            MagicMock(return_value=mock_client_inst),
        ):
            updates = await c.create_update(
                "AT",
                profile_ids=["p2"],
                text="hello world",
                scheduled_at="2026-05-27T12:00:00Z",
            )
        assert len(updates) == 1
        assert isinstance(updates[0], BufferUpdate)
        assert updates[0].id == "buf-update-1"
        assert updates[0].status == "pending"

    @pytest.mark.asyncio
    async def test_empty_profile_ids_raises(self):
        c = _client()
        with pytest.raises(ValueError):
            await c.create_update("AT", profile_ids=[], text="x")

    @pytest.mark.asyncio
    async def test_success_false_raises(self):
        c = _client()
        mock_client_inst = MagicMock()
        mock_client_inst.__aenter__ = AsyncMock(return_value=mock_client_inst)
        mock_client_inst.__aexit__ = AsyncMock(return_value=None)
        mock_client_inst.request = AsyncMock(
            return_value=_mock_response(json_body={"success": False, "message": "no profile"}),
        )
        with patch(
            "integrations.buffer_client.httpx.AsyncClient",
            MagicMock(return_value=mock_client_inst),
        ):
            with pytest.raises(BufferAPIError):
                await c.create_update("AT", profile_ids=["p1"], text="x")
