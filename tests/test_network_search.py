"""Tests for api.network._escape_ilike — wildcard escaping for ILIKE search.

The bug being guarded against is in api/network.py:search_people, where raw
user input ``q`` was interpolated into ``ilike("full_name", f"%{q}%")``. SQL
LIKE/ILIKE treats ``%`` and ``_`` as wildcards, so an unescaped ``q="%"``
would match every row — not classic SQLi (parameters are still bound), but
a semantic bug big enough to be worth a guard.

See docs/AUDIT_REVIEW_EXTERNAL_2026_05_12.md §3.8 (P2-5) and
docs/SYSTEM_AUDIT_2026_05_12.md §6.6.
"""
from __future__ import annotations

import pytest

from api.network import _escape_ilike


class TestEscapeIlike:
    def test_plain_string_is_unchanged(self) -> None:
        assert _escape_ilike("john") == "john"

    def test_percent_is_escaped(self) -> None:
        # f"%{_escape_ilike('100%')}%" must contain a literal % inside the
        # pattern, not a wildcard one.
        assert _escape_ilike("100%") == "100\\%"

    def test_underscore_is_escaped(self) -> None:
        assert _escape_ilike("under_score") == "under\\_score"

    def test_backslash_is_escaped_first(self) -> None:
        # Backslash must be escaped before the other characters so we don't
        # double-escape the backslashes the function itself inserts.
        assert _escape_ilike("back\\slash") == "back\\\\slash"

    def test_combined_specials(self) -> None:
        # Order check: ``\`` first, then ``%`` and ``_``. Each special must
        # appear escaped exactly once.
        assert _escape_ilike("a\\b%c_d") == "a\\\\b\\%c\\_d"

    def test_empty_string_returns_empty(self) -> None:
        assert _escape_ilike("") == ""

    def test_none_is_handled(self) -> None:
        # The wrapper at the callsite is gated by ``if q:`` so None never
        # actually reaches the helper in production — but the helper should
        # still be defensive.
        assert _escape_ilike(None) == ""  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("%", "\\%"),
            ("_", "\\_"),
            ("\\", "\\\\"),
            ("%%%", "\\%\\%\\%"),
            ("___", "\\_\\_\\_"),
            ("normal name", "normal name"),
            ("O'Brien", "O'Brien"),  # apostrophes are not LIKE specials
        ],
    )
    def test_parametrized_cases(self, raw: str, expected: str) -> None:
        assert _escape_ilike(raw) == expected
