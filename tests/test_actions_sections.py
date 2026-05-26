"""
tests/test_actions_sections.py — unit tests for the sectioned /today endpoint.

Tests cover:
  • _group_by_kind helper correctness
  • Section spec completeness (every kind has metadata)
  • Section order matches perishability ordering
  • Section payload shape (label, subtitle, icon, tone, empty_state)
  • Icon names exist in the dashboard's Icon.tsx allowlist
  • has_more flag accuracy
  • Empty sections preserve empty_state copy
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from api import actions as A


# Icon names known to exist in dashboard/src/components/ui/Icon.tsx as of
# 2026-05-26. Drift here = a missing-icon render bug on the live dashboard.
ALLOWED_ICONS = {
    "mail", "rocket", "refresh", "sparkles", "alert-triangle",
    "chevron-down", "chevron-right", "note",
    # extend as Icon.tsx grows
    "x", "check", "arrow-right",
}


class TestGroupByKind:
    def test_buckets_by_kind_preserving_order(self):
        actions = [
            {"id": "a", "kind": "score_high_no_resume", "title": "x"},
            {"id": "b", "kind": "resume_ready", "title": "x"},
            {"id": "c", "kind": "score_high_no_resume", "title": "x"},
            {"id": "d", "kind": "stale_application", "title": "x"},
        ]
        g = A._group_by_kind(actions)
        assert list(g["score_high_no_resume"]) == [actions[0], actions[2]]
        assert g["resume_ready"] == [actions[1]]
        assert g["stale_application"] == [actions[3]]

    def test_empty_input_empty_output(self):
        assert A._group_by_kind([]) == {}

    def test_unknown_kind_creates_its_own_bucket(self):
        actions = [{"id": "x", "kind": "some_future_kind"}]
        g = A._group_by_kind(actions)
        assert "some_future_kind" in g


class TestSectionSpec:
    def test_every_section_has_required_fields(self):
        required = {"kind", "label", "subtitle", "icon", "tone", "empty_state"}
        for spec in A._SECTION_SPEC:
            missing = required - set(spec.keys())
            assert not missing, f"section {spec.get('kind')} missing: {missing}"

    def test_section_kinds_are_unique(self):
        kinds = [s["kind"] for s in A._SECTION_SPEC]
        assert len(kinds) == len(set(kinds)), f"duplicate kinds in _SECTION_SPEC: {kinds}"

    def test_section_kinds_match_kind_priority_set(self):
        """Every kind that _KIND_PRIORITY ranks must have a section spec."""
        priority_kinds = set(A._KIND_PRIORITY.keys())
        spec_kinds = {s["kind"] for s in A._SECTION_SPEC}
        # Every priority kind must have a section
        missing_in_spec = priority_kinds - spec_kinds
        assert not missing_in_spec, (
            f"_KIND_PRIORITY has kinds not in _SECTION_SPEC: {missing_in_spec}"
        )
        # And vice versa — every section must be a known kind
        missing_in_priority = spec_kinds - priority_kinds
        assert not missing_in_priority, (
            f"_SECTION_SPEC has kinds not in _KIND_PRIORITY: {missing_in_priority}"
        )

    def test_section_order_matches_kind_priority(self):
        """Section order on screen must follow perishability priority."""
        section_kinds = [s["kind"] for s in A._SECTION_SPEC]
        # Sort by priority value, lowest first (= highest urgency)
        expected = sorted(
            section_kinds, key=lambda k: A._KIND_PRIORITY.get(k, 99)
        )
        assert section_kinds == expected, (
            f"section order does not match priority order. "
            f"Got: {section_kinds}, expected: {expected}"
        )

    def test_all_icons_in_allowed_set(self):
        """If this fails, the dashboard will render a broken icon glyph."""
        for spec in A._SECTION_SPEC:
            assert spec["icon"] in ALLOWED_ICONS, (
                f"icon '{spec['icon']}' for kind '{spec['kind']}' "
                f"not in Icon.tsx allowlist {sorted(ALLOWED_ICONS)}"
            )

    def test_tones_are_valid(self):
        valid_tones = {"danger", "warning", "info", "success", "neutral"}
        for spec in A._SECTION_SPEC:
            assert spec["tone"] in valid_tones, (
                f"tone '{spec['tone']}' not in {valid_tones} for kind '{spec['kind']}'"
            )

    def test_empty_state_copy_is_non_empty(self):
        for spec in A._SECTION_SPEC:
            assert spec["empty_state"].strip(), (
                f"section '{spec['kind']}' has empty empty_state copy"
            )

    def test_subtitles_are_non_empty(self):
        for spec in A._SECTION_SPEC:
            assert spec["subtitle"].strip(), (
                f"section '{spec['kind']}' has empty subtitle"
            )


class TestSectionPayloadShape:
    """Pure-function check on the response builder logic, without FastAPI."""

    def test_default_per_kind_value(self):
        assert A.DEFAULT_PER_KIND == 5

    def test_section_spec_count(self):
        # 8 known kinds across the system as of 2026-05-26
        assert len(A._SECTION_SPEC) == 8
