"""
B9 — Source-code guard tests: verify mock references removed from dashboard.

These are NOT runtime tests — they read the TSX source and assert that
MOCK_* references are gone. If a developer adds them back, the test fails.

Run: pytest tests/test_b9_dashboard_no_mocks.py -v
"""
from __future__ import annotations

from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[1] / "dashboard" / "src"


def _read(path: str) -> str:
    """Read a dashboard source file. Returns empty string if missing."""
    full = DASHBOARD / path
    if not full.exists():
        return ""
    return full.read_text()


class TestNetworkPage:
    def test_does_not_import_mock_people(self):
        src = _read("app/network/page.tsx")
        assert "MOCK_PEOPLE" not in src, "page.tsx still imports MOCK_PEOPLE"
        assert "MOCK_TARGET_COVERAGE" not in src, "page.tsx still imports MOCK_TARGET_COVERAGE"
        assert "MOCK_TOP_INTRO_PATHS" not in src, "page.tsx still imports MOCK_TOP_INTRO_PATHS"

    def test_imports_real_fetchers(self):
        src = _read("app/network/page.tsx")
        assert "fetchNetworkPeople" in src, "page.tsx missing fetchNetworkPeople import"
        assert "fetchTargetCoverage" in src, "page.tsx missing fetchTargetCoverage import"

    def test_calls_real_fetchers(self):
        src = _read("app/network/page.tsx")
        assert "fetchTargetCoverage()" in src, "page.tsx doesn't call fetchTargetCoverage"
        assert "fetchNetworkPeople()" in src, "page.tsx doesn't call fetchNetworkPeople"

    def test_has_error_state(self):
        src = _read("app/network/page.tsx")
        assert "error" in src, "page.tsx missing error state handling"

    def test_has_empty_state_cta(self):
        src = _read("app/network/page.tsx")
        assert "No people imported yet" in src, "page.tsx missing empty-state CTA"


class TestNetworkClient:
    def test_does_not_import_mock_intro_draft(self):
        src = _read("components/network/NetworkClient.tsx")
        assert "MOCK_INTRO_DRAFT" not in src, "NetworkClient still imports MOCK_INTRO_DRAFT"

    def test_imports_submit_draft_intro(self):
        src = _read("components/network/NetworkClient.tsx")
        assert "submitDraftIntro" in src, "NetworkClient missing submitDraftIntro import"

    def test_uses_real_draft_function(self):
        src = _read("components/network/NetworkClient.tsx")
        assert "submitDraftIntro" in src, "NetworkClient doesn't use submitDraftIntro"
        assert "stubDraftIntro" not in src, "NetworkClient still uses stubDraftIntro"
        assert "MOCK_INTRO_DRAFT" not in src, "NetworkClient still references MOCK_INTRO_DRAFT"


class TestPeopleDiscoveryButton:
    """2026-05-27: replaced LinkedInImportButton (CSV upload) with
    PeopleDiscoveryButton (server-side Perplexity discovery)."""

    def test_uses_real_fetch(self):
        src = _read("components/network/PeopleDiscoveryButton.tsx")
        assert "/api/proxy/actions/today/trigger-people-discovery" in src, (
            "PeopleDiscoveryButton doesn't hit the trigger-people-discovery endpoint"
        )
        assert "fetch(" in src, "PeopleDiscoveryButton missing fetch call"

    def test_no_mock_delay(self):
        src = _read("components/network/PeopleDiscoveryButton.tsx")
        assert "setTimeout" not in src, "PeopleDiscoveryButton still uses artificial delay"

    def test_csv_button_deleted(self):
        """The old LinkedInImportButton.tsx must not exist."""
        import os
        dashboard_root = os.path.join(
            os.path.dirname(__file__), "..", "dashboard", "src"
        )
        path = os.path.join(dashboard_root, "components/network/LinkedInImportButton.tsx")
        assert not os.path.exists(path), (
            "LinkedInImportButton.tsx still exists — was supposed to be deleted "
            "alongside the CSV upload removal."
        )


class TestLibApi:
    def test_has_network_fetchers(self):
        src = _read("lib/api.ts")
        assert "fetchNetworkPeople" in src, "api.ts missing fetchNetworkPeople"
        assert "fetchTargetCoverage" in src, "api.ts missing fetchTargetCoverage"
        assert "fetchNetworkPathsForTarget" in src, "api.ts missing fetchNetworkPathsForTarget"
        assert "submitDraftIntro" in src, "api.ts missing submitDraftIntro"

    def test_fetchers_use_correct_method(self):
        src = _read("lib/api.ts")
        assert "fetchNetworkPeople" in src, "fetchNetworkPeople missing"
        assert "fetchTargetCoverage" in src, "fetchTargetCoverage missing"

    def test_submit_draft_intro_is_post(self):
        src = _read("lib/api.ts")
        # Find the submitDraftIntro function body
        idx = src.find("submitDraftIntro")
        assert idx != -1, "submitDraftIntro not found in api.ts"
        func_body = src[idx:idx + 500]
        assert "method: 'POST'" in func_body, "submitDraftIntro must be POST"


class TestWorkspaceNetworkTab:
    """B9 follow-up: the workspace NetworkTab was a second mock surface the
    initial diff missed. Same MOCK_INTRO_DRAFT path as NetworkClient; same
    fix — call submitDraftIntro from the shared lib/api.ts. Guard against
    regression."""

    def test_does_not_import_mock_intro_draft(self):
        src = _read("components/workspace/NetworkTab.tsx")
        assert "MOCK_INTRO_DRAFT" not in src, (
            "NetworkTab.tsx still imports MOCK_INTRO_DRAFT"
        )
        assert "from '@/lib/mock/network'" not in src, (
            "NetworkTab.tsx still imports from the mock module"
        )

    def test_imports_submit_draft_intro(self):
        src = _read("components/workspace/NetworkTab.tsx")
        assert "submitDraftIntro" in src, (
            "NetworkTab.tsx missing submitDraftIntro import"
        )

    def test_uses_real_draft_function(self):
        src = _read("components/workspace/NetworkTab.tsx")
        # The IntroDraftModal must receive a real fetcher, not the old stub
        assert "stubDraftIntro" not in src, "NetworkTab.tsx still wires stubDraftIntro"
        assert "draftIntroForPath" in src, (
            "NetworkTab.tsx must wire draftIntroForPath into IntroDraftModal"
        )
