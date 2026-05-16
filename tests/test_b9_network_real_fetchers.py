"""
B9 — /network real fetchers + POST /network/draft-intro endpoint tests.

Run: pytest tests/test_b9_network_real_fetchers.py -v
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

# Only stub google.genai (the one optional dep not installed in the venv).
# DO NOT overwrite sys.modules["db.client"] / sys.modules["api.context"]
# at module load time — those leak into the collection phase of other
# test files (g6, traces, persona_synthesizer, …) and crash ~150 tests.
# The handlers that need a mocked supabase patch `api.network.get_supabase`
# inside the test body via unittest.mock.patch.
if "google.genai" not in sys.modules:
    sys.modules["google.genai"] = MagicMock()


from api.network import (
    DraftIntroBody,
    DraftIntroResponse,
    router,
)
from pydantic import ValidationError


# ── Helpers ──────────────────────────────────────────────────────────────

def _find_route(path: str, method: str = "GET"):
    for r in router.routes:
        if path in str(r.path) and method.upper() in list(r.methods or []):
            return r
    return None


# ── Tests ────────────────────────────────────────────────────────────────

class TestEndpointExists:
    def test_draft_intro_route_registered(self):
        route = _find_route("/draft-intro", "POST")
        assert route is not None, "POST /network/draft-intro not registered"

    def test_draft_intro_has_response_model(self):
        route = _find_route("/draft-intro", "POST")
        # FastAPI APIRoute has response_model
        assert hasattr(route, "response_model"), "route missing response_model"
        assert route.response_model is DraftIntroResponse


class TestRequestBodyValidation:
    def test_valid_body_parses(self):
        from uuid import uuid4
        tcid = uuid4()
        ipid = uuid4()
        body = DraftIntroBody(
            target_company_id=tcid,
            introducer_person_id=ipid,
            target_role_summary="Senior Engineer",
        )
        assert str(body.target_company_id) == str(tcid)
        assert str(body.introducer_person_id) == str(ipid)
        assert body.target_role_summary == "Senior Engineer"

    def test_extra_fields_forbidden(self):
        from uuid import uuid4
        with pytest.raises(ValidationError) as exc_info:
            DraftIntroBody(
                target_company_id=uuid4(),
                introducer_person_id=uuid4(),
                evil_field="x",
            )
        assert "extra_forbidden" in str(exc_info.value) or "Extra inputs are not permitted" in str(exc_info.value)

    def test_extra_fields_forbidden_alternate_name(self):
        from uuid import uuid4
        with pytest.raises(ValidationError):
            DraftIntroBody(
                target_company_id=uuid4(),
                introducer_person_id=uuid4(),
                targetCompanyId="different-uuid",  # wrong case
            )

    def test_target_role_summary_optional(self):
        from uuid import uuid4
        body = DraftIntroBody(
            target_company_id=uuid4(),
            introducer_person_id=uuid4(),
        )
        assert body.target_role_summary is None


class TestResponseModel:
    def test_response_has_all_fields(self):
        r = DraftIntroResponse(
            subject="Intro request",
            body="Hi, could you introduce me...",
            introducer_name="Alice Smith",
            target_company_name="Stripe",
            confidence=0.85,
            cost_usd=0.03,
        )
        assert r.subject == "Intro request"
        assert r.introducer_name == "Alice Smith"
        assert r.target_company_name == "Stripe"
        assert r.confidence == 0.85
        assert r.cost_usd == 0.03


class TestEndpointHandler:
    def test_draft_intro_404_when_no_target_company(self):
        """When target company doesn't belong to user, return 404."""
        from uuid import uuid4
        from fastapi import HTTPException
        from api.network import draft_intro

        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

        body = DraftIntroBody(
            target_company_id=uuid4(),
            introducer_person_id=uuid4(),
        )
        user = MagicMock()
        user.id = uuid4()

        with patch("api.network.get_supabase", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                import asyncio
                asyncio.run(draft_intro(body=body, user=user))

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "no_intro_path_to_target"

    def test_draft_intro_404_when_no_matching_path(self):
        """When no path through the introducer exists, return 404."""
        from uuid import uuid4
        from fastapi import HTTPException
        from api.network import draft_intro

        tcid = uuid4()
        ipid = uuid4()
        uid = uuid4()

        # Target company exists
        db = MagicMock()
        db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(
            data=[{"id": str(tcid), "name": "Stripe", "company_name": "Stripe"}]
        )

        with patch("api.network.ReferralGraph") as MockRG, \
             patch("api.network.get_supabase", return_value=db):
            mock_rg = MagicMock()
            mock_rg.find_paths.return_value = []
            MockRG.return_value = mock_rg

            body = DraftIntroBody(
                target_company_id=tcid,
                introducer_person_id=ipid,
            )
            user = MagicMock()
            user.id = uid

            with pytest.raises(HTTPException) as exc_info:
                import asyncio
                asyncio.run(draft_intro(body=body, user=user))

            assert exc_info.value.status_code == 404
            assert exc_info.value.detail == "no_intro_path_to_target"

    def test_draft_intro_404_when_company_belongs_to_other_user(self):
        """When target company query returns empty (belongs to other user), 404."""
        from uuid import uuid4
        from fastapi import HTTPException
        from api.network import draft_intro

        db = MagicMock()
        # First query (target company) returns empty
        db.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value = MagicMock(data=[])

        body = DraftIntroBody(
            target_company_id=uuid4(),
            introducer_person_id=uuid4(),
        )
        user = MagicMock()
        user.id = uuid4()

        with patch("api.network.get_supabase", return_value=db):
            with pytest.raises(HTTPException) as exc_info:
                import asyncio
                asyncio.run(draft_intro(body=body, user=user))

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "no_intro_path_to_target"
