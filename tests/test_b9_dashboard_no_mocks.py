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


class TestPeopleFinder:
    """Network discovery uses the Apollo people-finder against real backend
    routes — the LinkedIn CSV upload was removed (2026-05-31)."""

    def test_finder_posts_to_real_endpoints(self):
        src = _read("components/network/PeopleFinderModal.tsx")
        assert "/api/proxy/apollo/search-people" in src, "finder doesn't call Apollo search"
        assert "/api/proxy/network/people" in src, "finder doesn't add via /network/people"

    def test_no_mock_fallback(self):
        src = _read("components/network/PeopleFinderModal.tsx")
        assert "MOCK_IMPORT_SUMMARY" not in src
        assert "setTimeout" not in src

    def test_csv_upload_button_is_gone(self):
        # The old CSV component must not exist, and no surface may reference it.
        assert _read("components/network/LinkedInImportButton.tsx") == ""
        for rel in ("components/network/NetworkClient.tsx",
                    "components/workspace/NetworkTab.tsx"):
            src = _read(rel)
            assert "LinkedInImportButton" not in src
            assert "import/linkedin-csv" not in src


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
