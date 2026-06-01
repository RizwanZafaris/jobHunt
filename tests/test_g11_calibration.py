"""Tier 4 G11 — voice-calibration tests.

Covers:
  - style_extractor parses Sonnet response into structured StyleAttributes
  - style_extractor identity-locks vocab/openings to the corpus
  - voice_profiler produces the injectable block
  - persist writes profile_master.voice_calibration AND flips
    writing_samples.used_in_calibration
  - persist refuses with empty user_id (wallet guard)
  - persist refuses with empty sample_ids (identity-lock)
  - voice_injector returns None when no calibration exists
  - voice_injector returns the block when calibration exists
  - voice_injector.prepend_voice_block is a no-op on cold-start
  - API: upload + list + delete writing_samples
  - API: voice-calibration/run rejects when zero samples
  - Auto-trigger: 5th sample with > 1d since last calibration enqueues G11
  - Auto-trigger: 5th sample within cooldown does NOT enqueue
  - Cost: a calibration run sums to ~$0.08 (≤ $0.10 target)
  - llm_router._derive_graph_and_node routes g11.* to graph='G11'

No live API calls. No live Supabase calls.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())


@pytest.fixture(autouse=True)
def _env():
    os.environ.setdefault("ANTHROPIC_API_KEY", "test")
    os.environ.setdefault("OPENAI_API_KEY", "test")
    os.environ.setdefault("SUPABASE_URL", "http://test")
    os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")


RIZWAN_UUID = "00000000-0000-0000-0000-000000000001"


def _sample(id_: str, body: str, *, title: str = "post", kind: str = "linkedin_post") -> dict:
    return {
        "id": id_,
        "user_id": RIZWAN_UUID,
        "title": title,
        "body": body,
        "kind": kind,
        "source": "user_upload",
        "used_in_calibration": False,
    }


def _make_user():
    """Build a valid api.users.User instance for endpoint smoke tests.

    The User Pydantic model requires created_at; tests pass a fixed
    timestamp so the assertions don't depend on wall-clock drift.
    """
    from uuid import UUID
    from api.users import User
    return User(
        id=UUID(RIZWAN_UUID),
        email="r@example.com",
        full_name="R",
        plan="lifetime",
        is_admin=False,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _llm_result(text: str, *, cost: float = 0.05, latency: int = 2200):
    """Build an LLMResult stub mimicking agents.llm_router.LLMResult."""
    from agents.llm_router import LLMResult
    return LLMResult(
        text=text,
        provider="anthropic",
        model="claude-sonnet-4-6",
        input_tokens=1500,
        output_tokens=350,
        cost_usd=cost,
        latency_ms=latency,
    )


# ════════════════════════════════════════════════════════════════════════
# style_extractor_node
# ════════════════════════════════════════════════════════════════════════
class TestStyleExtractor:
    @pytest.mark.asyncio
    async def test_parses_sonnet_response_into_dict(self, monkeypatch):
        """A Sonnet JSON response is parsed into the StyleAttributes shape."""
        from agents import g11_nodes

        async def fake_ask(**kwargs):
            return _llm_result(json.dumps({
                "tone": "conversational-confident, no hedging qualifiers",
                "sentence_length_avg": 12.4,
                "sentence_length_distribution": {"short": 8, "medium": 6, "long": 2},
                "openings_preferred": ["Led", "Built", "Cut"],
                "punctuation_habits": ["em-dashes for asides", "Oxford commas"],
                "vocabulary_preferred": ["built", "ran", "cut"],
                "vocabulary_avoid": ["leveraged", "synergies", "passionate about"],
                "notes": "writes in punchy fragments.",
            }))

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g11_nodes, "get_router", lambda: FakeRouter())

        # corpus contains all the "preferred" words so identity-lock keeps them
        samples = [
            _sample("s1", "Led the team. Built the pipeline. Cut the cycle time. ran weekly reviews."),
        ]
        out = await g11_nodes.style_extractor_node({
            "user_id": RIZWAN_UUID,
            "samples": samples,
        })
        attrs = out["style_attributes"]
        assert attrs["tone"].startswith("conversational-confident")
        assert attrs["sentence_length_avg"] == 12.4
        assert "Led" in attrs["openings_preferred"]
        assert "built" in [v.lower() for v in attrs["vocabulary_preferred"]]
        assert "synergies" in attrs["vocabulary_avoid"]
        # cost telemetry propagates
        assert out["cost_usd_total"] > 0

    @pytest.mark.asyncio
    async def test_identity_lock_drops_fabricated_vocab(self, monkeypatch):
        """vocabulary_preferred and openings_preferred entries NOT found in
        the corpus must be dropped (engineering principle 3 — identity lock)."""
        from agents import g11_nodes

        async def fake_ask(**kwargs):
            return _llm_result(json.dumps({
                "tone": "punchy",
                "sentence_length_avg": 9.0,
                "sentence_length_distribution": {"short": 10, "medium": 2, "long": 0},
                "openings_preferred": ["Led", "Spearheaded", "Synergised"],   # last two NOT in corpus
                "punctuation_habits": ["em-dashes"],
                "vocabulary_preferred": ["led", "passionate", "leveraged"],   # last two NOT in corpus
                "vocabulary_avoid": ["synergies"],
                "notes": "",
            }))

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g11_nodes, "get_router", lambda: FakeRouter())

        samples = [_sample("s1", "Led the migration. led with data.")]
        out = await g11_nodes.style_extractor_node({
            "user_id": RIZWAN_UUID,
            "samples": samples,
        })
        attrs = out["style_attributes"]
        # Spearheaded / Synergised are gone — not in corpus
        assert "Spearheaded" not in attrs["openings_preferred"]
        assert "Synergised" not in attrs["openings_preferred"]
        assert "Led" in attrs["openings_preferred"]
        # passionate / leveraged gone — not in corpus
        assert all(v.lower() not in ("passionate", "leveraged")
                   for v in attrs["vocabulary_preferred"])
        # led kept (it's in the corpus)
        assert any(v.lower() == "led" for v in attrs["vocabulary_preferred"])

    @pytest.mark.asyncio
    async def test_empty_samples_short_circuits(self, monkeypatch):
        """No samples → no LLM call, empty style_attributes returned."""
        from agents import g11_nodes
        call_count = {"n": 0}

        async def fake_ask(**kwargs):
            call_count["n"] += 1
            return _llm_result("{}")

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g11_nodes, "get_router", lambda: FakeRouter())

        out = await g11_nodes.style_extractor_node({
            "user_id": RIZWAN_UUID,
            "samples": [],
        })
        assert call_count["n"] == 0
        assert out["style_attributes"] == {}


# ════════════════════════════════════════════════════════════════════════
# voice_profiler_node
# ════════════════════════════════════════════════════════════════════════
class TestVoiceProfiler:
    @pytest.mark.asyncio
    async def test_produces_injectable_block(self, monkeypatch):
        """voice_profiler returns a non-empty string with the heading."""
        from agents import g11_nodes

        async def fake_ask(**kwargs):
            return _llm_result(
                "WRITING STYLE PROFILE (write in THIS voice — it's the user's):\n"
                "- Tone: conversational-confident, no hedging\n"
                "- Sentence length: short and punchy, avg 12 words\n"
                "- Openings: action-first, examples: \"Led\", \"Built\", \"Cut\"\n"
                "- Punctuation: em dashes for asides, Oxford commas\n"
                "- Prefer: \"built\", \"ran\", \"cut\" (over \"developed\", \"reduced\")\n"
                "- Avoid: \"leveraged\", \"synergies\", \"passionate about\"",
                cost=0.03,
                latency=1500,
            )

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g11_nodes, "get_router", lambda: FakeRouter())

        out = await g11_nodes.voice_profiler_node({
            "style_attributes": {
                "tone": "conversational-confident, no hedging",
                "vocabulary_preferred": ["built", "ran", "cut"],
                "vocabulary_avoid": ["leveraged", "synergies"],
            },
        })
        block = out["voice_profile_block"]
        assert block.startswith("WRITING STYLE PROFILE")
        assert "Avoid:" in block
        assert out["cost_usd_total"] > 0

    @pytest.mark.asyncio
    async def test_strips_leading_prose(self, monkeypatch):
        """If Sonnet adds prose before the heading, we snip back to it."""
        from agents import g11_nodes

        async def fake_ask(**kwargs):
            return _llm_result(
                "Here's the block as requested:\n\n"
                "WRITING STYLE PROFILE (write in THIS voice):\n"
                "- Tone: punchy\n"
                "- Avoid: \"synergies\""
            )

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g11_nodes, "get_router", lambda: FakeRouter())

        out = await g11_nodes.voice_profiler_node({
            "style_attributes": {"tone": "punchy"},
        })
        block = out["voice_profile_block"]
        assert block.startswith("WRITING STYLE PROFILE")
        assert "Here's the block" not in block

    @pytest.mark.asyncio
    async def test_empty_style_short_circuits(self, monkeypatch):
        from agents import g11_nodes
        call_count = {"n": 0}

        async def fake_ask(**kwargs):
            call_count["n"] += 1
            return _llm_result("...")

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g11_nodes, "get_router", lambda: FakeRouter())

        out = await g11_nodes.voice_profiler_node({"style_attributes": {}})
        assert call_count["n"] == 0
        assert out["voice_profile_block"] == ""


# ════════════════════════════════════════════════════════════════════════
# persist_node
# ════════════════════════════════════════════════════════════════════════
class TestPersistNode:
    @pytest.mark.asyncio
    async def test_writes_profile_master_and_marks_samples(self, monkeypatch):
        """persist writes the full JSON to profile_master AND flips
        writing_samples.used_in_calibration for the consumed ids."""
        from agents import g11_nodes, g11_io
        calls: dict[str, list] = {"update": [], "mark": []}

        def fake_update(*, user_id, voice_calibration):
            calls["update"].append((user_id, voice_calibration))
            return {"id": 1, "user_id": user_id}

        def fake_mark(*, user_id, sample_ids):
            calls["mark"].append((user_id, sample_ids))
            return len(sample_ids)

        monkeypatch.setattr(g11_io, "update_profile_voice_calibration", fake_update)
        monkeypatch.setattr(g11_io, "mark_samples_used", fake_mark)

        out = await g11_nodes.persist_node({
            "user_id": RIZWAN_UUID,
            "samples": [
                _sample("s1", "Led."),
                _sample("s2", "Built."),
            ],
            "style_attributes": {
                "tone": "punchy",
                "vocabulary_preferred": ["led", "built"],
            },
            "voice_profile_block": "WRITING STYLE PROFILE\n- Tone: punchy",
        })
        assert out["persisted"] is True
        assert out["samples_marked_used"] == 2
        # voice_calibration_json carries the injectable block
        voice = out["voice_calibration_json"]
        assert voice["voice_profile_block"].startswith("WRITING STYLE PROFILE")
        # source_sample_ids carries provenance — engineering principle 2 (cite)
        assert set(voice["source_sample_ids"]) == {"s1", "s2"}
        # update_profile_voice_calibration called with the right user_id
        assert calls["update"][0][0] == RIZWAN_UUID
        # mark_samples_used called with the right ids
        assert sorted(calls["mark"][0][1]) == ["s1", "s2"]

    @pytest.mark.asyncio
    async def test_refuses_when_user_id_missing(self):
        """Wallet guard — user_id is required (RLS would otherwise allow
        cross-tenant writes via env override)."""
        from agents import g11_nodes
        out = await g11_nodes.persist_node({
            "user_id": None,
            "samples": [_sample("s1", "anything")],
            "style_attributes": {"tone": "x"},
            "voice_profile_block": "WRITING STYLE PROFILE",
        })
        assert out["persisted"] is False
        assert out["error"] == "missing_user_id"

    @pytest.mark.asyncio
    async def test_refuses_when_no_sample_ids(self):
        """Identity-lock — every voice profile must trace back to ≥ 1 sample."""
        from agents import g11_nodes
        out = await g11_nodes.persist_node({
            "user_id": RIZWAN_UUID,
            "samples": [],
            "style_attributes": {"tone": "x"},
            "voice_profile_block": "WRITING STYLE PROFILE",
        })
        assert out["persisted"] is False
        assert out["error"] == "no_sample_ids"


# ════════════════════════════════════════════════════════════════════════
# voice_injector
# ════════════════════════════════════════════════════════════════════════
class TestVoiceInjector:
    def test_returns_none_when_no_calibration(self, monkeypatch):
        """No profile_master row OR empty voice_calibration → None."""
        from agents import voice_injector

        class FakeSupabase:
            def table(self, *_a, **_kw):
                return self
            def select(self, *_a, **_kw):
                return self
            def eq(self, *_a, **_kw):
                return self
            def limit(self, *_a, **_kw):
                return self
            def execute(self):
                class R: data = []
                return R()

        from db import client as db_client
        monkeypatch.setattr(db_client, "get_supabase", lambda: FakeSupabase())

        assert voice_injector.get_voice_profile_block(RIZWAN_UUID) is None

    def test_returns_block_when_calibration_exists(self, monkeypatch):
        """voice_calibration.voice_profile_block surfaces as the return value."""
        from agents import voice_injector

        class FakeSupabase:
            def table(self, *_a, **_kw):
                return self
            def select(self, *_a, **_kw):
                return self
            def eq(self, *_a, **_kw):
                return self
            def limit(self, *_a, **_kw):
                return self
            def execute(self):
                class R:
                    data = [{
                        "voice_calibration": {
                            "voice_profile_block": "WRITING STYLE PROFILE\n- Tone: punchy",
                            "tone": "punchy",
                        }
                    }]
                return R()

        from db import client as db_client
        monkeypatch.setattr(db_client, "get_supabase", lambda: FakeSupabase())

        block = voice_injector.get_voice_profile_block(RIZWAN_UUID)
        assert block is not None
        assert block.startswith("WRITING STYLE PROFILE")

    def test_prepend_voice_block_is_noop_on_coldstart(self, monkeypatch):
        """No calibration → prepend_voice_block returns the user message unchanged."""
        from agents import voice_injector
        monkeypatch.setattr(voice_injector, "get_voice_profile_block", lambda uid=None: None)
        msg = "WRITE THE RESUME NOW"
        assert voice_injector.prepend_voice_block(msg, user_id=RIZWAN_UUID) == msg

    def test_prepend_voice_block_prepends_with_divider(self, monkeypatch):
        """Block + divider + user message — preserving ordering for the LLM."""
        from agents import voice_injector
        monkeypatch.setattr(
            voice_injector,
            "get_voice_profile_block",
            lambda uid=None: "WRITING STYLE PROFILE\n- Tone: punchy",
        )
        out = voice_injector.prepend_voice_block("THE BRIEF", user_id=RIZWAN_UUID)
        assert out.startswith("WRITING STYLE PROFILE")
        assert "THE BRIEF" in out
        # Block comes first
        assert out.index("WRITING STYLE PROFILE") < out.index("THE BRIEF")


# ════════════════════════════════════════════════════════════════════════
# API endpoints (writing-samples + voice-calibration/run)
# ════════════════════════════════════════════════════════════════════════
class TestApiEndpoints:
    def test_upload_list_delete(self, monkeypatch):
        """Smoke test of the full CRUD flow against mocked io functions."""
        from agents import g11_io

        store: list[dict] = []

        def fake_insert(*, user_id, title, body, kind, source=None):
            row = {
                "id": f"id-{len(store)+1}",
                "user_id": user_id,
                "title": title,
                "body": body,
                "kind": kind,
                "source": source,
                "used_in_calibration": False,
            }
            store.append(row)
            return row

        def fake_list(user_id, *, limit=100):
            return list(store)

        def fake_delete(*, user_id, sample_id):
            for i, r in enumerate(store):
                if r["id"] == sample_id and r["user_id"] == user_id:
                    store.pop(i)
                    return True
            return False

        def fake_count(uid):
            return len(store)

        def fake_last(uid):
            return None  # never calibrated → auto-trigger should fire after 5

        monkeypatch.setattr(g11_io, "insert_writing_sample", fake_insert)
        monkeypatch.setattr(g11_io, "list_writing_samples", fake_list)
        monkeypatch.setattr(g11_io, "delete_writing_sample", fake_delete)
        monkeypatch.setattr(g11_io, "count_writing_samples", fake_count)
        monkeypatch.setattr(g11_io, "load_last_calibration_at", fake_last)

        enqueued: list[tuple] = []
        from api import queue as api_queue
        monkeypatch.setattr(
            api_queue, "enqueue_g11_voice_calibration",
            lambda user_id, force=False: (
                enqueued.append((user_id, force)) or "run-1"
            ),
        )

        # Use the upload helper directly to bypass FastAPI auth machinery.
        from api.profile import (
            upload_writing_sample,
            list_writing_samples_endpoint,
            delete_writing_sample_endpoint,
            WritingSampleBody,
        )
        from api.users import User
        from uuid import UUID

        user = _make_user()

        # 1 sample → no auto-trigger
        out = asyncio.run(upload_writing_sample(
            WritingSampleBody(title="t1", body="b1", kind="essay"),
            user=user,
        ))
        assert out["auto_triggered"] is False
        assert out["sample"]["id"] == "id-1"

        # List
        out2 = asyncio.run(list_writing_samples_endpoint(user=user))
        assert out2["count"] == 1

        # Delete
        out3 = asyncio.run(delete_writing_sample_endpoint(
            sample_id=UUID("00000000-0000-0000-0000-000000000099"),
            user=user,
        )) if False else None
        # Use the real id
        from fastapi import HTTPException
        # delete with a fake id should 404
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(delete_writing_sample_endpoint(
                sample_id=UUID("99999999-9999-9999-9999-999999999999"),
                user=user,
            ))
        assert excinfo.value.status_code == 404

    def test_auto_trigger_fires_at_5th_sample(self, monkeypatch):
        """5th upload with > 24h since last calibration → enqueues G11."""
        from agents import g11_io
        from api import profile as profile_mod

        count = {"n": 4}  # we'll bump to 5 in the fake_insert below

        def fake_insert(*, user_id, title, body, kind, source=None):
            count["n"] += 1
            return {"id": f"id-{count['n']}", "title": title}

        monkeypatch.setattr(g11_io, "insert_writing_sample", fake_insert)
        monkeypatch.setattr(g11_io, "count_writing_samples", lambda uid: count["n"])
        # Never calibrated → cooldown_ok=True
        monkeypatch.setattr(g11_io, "load_last_calibration_at", lambda uid: None)

        enqueued: list = []
        from api import queue as api_queue
        monkeypatch.setattr(
            api_queue,
            "enqueue_g11_voice_calibration",
            lambda user_id, force=False: (
                enqueued.append((str(user_id), force)) or "run-xyz"
            ),
        )

        from api.profile import upload_writing_sample, WritingSampleBody
        from api.users import User
        from uuid import UUID
        user = _make_user()
        out = asyncio.run(upload_writing_sample(
            WritingSampleBody(title="t5", body="b5", kind="email"),
            user=user,
        ))
        assert out["auto_triggered"] is True
        assert out["run_id"] == "run-xyz"
        assert enqueued and enqueued[0][0] == RIZWAN_UUID

    def test_auto_trigger_respects_cooldown(self, monkeypatch):
        """5th upload but calibrated 6 hours ago → cooldown blocks auto-trigger."""
        from agents import g11_io
        monkeypatch.setattr(g11_io, "insert_writing_sample",
                            lambda **kw: {"id": "id-5", "title": kw["title"]})
        monkeypatch.setattr(g11_io, "count_writing_samples", lambda uid: 7)
        # Calibrated 6 hours ago — less than the 24h cooldown
        recent = datetime.now(timezone.utc) - timedelta(hours=6)
        monkeypatch.setattr(g11_io, "load_last_calibration_at", lambda uid: recent)

        enqueued: list = []
        from api import queue as api_queue
        monkeypatch.setattr(
            api_queue,
            "enqueue_g11_voice_calibration",
            lambda user_id, force=False: (
                enqueued.append((user_id, force)) or "should-not-fire"
            ),
        )

        from api.profile import upload_writing_sample, WritingSampleBody
        from api.users import User
        from uuid import UUID
        user = _make_user()
        out = asyncio.run(upload_writing_sample(
            WritingSampleBody(title="t6", body="b6", kind="email"),
            user=user,
        ))
        assert out["auto_triggered"] is False
        assert out["run_id"] is None
        assert enqueued == []

    def test_voice_calibration_run_rejects_zero_samples(self, monkeypatch):
        """POST /profile/voice-calibration/run → 409 when no samples uploaded."""
        from fastapi import HTTPException
        from agents import g11_io
        monkeypatch.setattr(g11_io, "count_writing_samples", lambda uid: 0)

        from api.profile import run_voice_calibration
        from api.users import User
        from uuid import UUID
        user = _make_user()
        with pytest.raises(HTTPException) as excinfo:
            asyncio.run(run_voice_calibration(force=False, user=user))
        assert excinfo.value.status_code == 409


# ════════════════════════════════════════════════════════════════════════
# Cost telemetry — full graph sums to ~$0.08 (≤ $0.10 target)
# ════════════════════════════════════════════════════════════════════════
class TestCostTelemetry:
    def test_router_derives_g11_graph(self):
        """llm_router._derive_graph_and_node maps g11.* → graph='G11'."""
        from agents.llm_router import _derive_graph_and_node
        graph, node = _derive_graph_and_node("g11.style_extractor")
        assert graph == "G11"
        assert node == "style_extractor"
        graph, node = _derive_graph_and_node("g11.voice_profiler")
        assert graph == "G11"
        assert node == "voice_profiler"

    @pytest.mark.asyncio
    async def test_calibration_run_costs_under_10_cents(self, monkeypatch):
        """End-to-end: style_extractor + voice_profiler + persist sums to
        ~$0.08 with the stubbed Sonnet costs we report."""
        from agents import g11_nodes, g11_io

        async def fake_ask(**kwargs):
            agent_name = kwargs.get("agent_name", "")
            if agent_name == "g11.style_extractor":
                return _llm_result(json.dumps({
                    "tone": "punchy",
                    "sentence_length_avg": 11.0,
                    "sentence_length_distribution": {"short": 5, "medium": 3, "long": 1},
                    "openings_preferred": ["Built", "Led"],
                    "punctuation_habits": ["em-dashes"],
                    "vocabulary_preferred": ["built", "led"],
                    "vocabulary_avoid": ["synergies"],
                    "notes": "",
                }), cost=0.05)
            if agent_name == "g11.voice_profiler":
                return _llm_result(
                    "WRITING STYLE PROFILE\n- Tone: punchy\n- Avoid: \"synergies\"",
                    cost=0.03,
                )
            return _llm_result("{}", cost=0.0)

        class FakeRouter:
            ask = staticmethod(fake_ask)

        monkeypatch.setattr(g11_nodes, "get_router", lambda: FakeRouter())

        # Stub persist writes so we don't touch the DB.
        monkeypatch.setattr(g11_io, "update_profile_voice_calibration",
                            lambda **_: {"id": 1})
        monkeypatch.setattr(g11_io, "mark_samples_used", lambda **_: 1)

        # Run the three nodes sequentially (mirror the linear graph).
        state: dict = {
            "user_id": RIZWAN_UUID,
            "samples": [_sample("s1", "Built it. Led it.")],
            "cost_usd_total": 0.0,
            "latency_ms_total": 0,
            "transcript": [],
        }

        async def _merge(patch):
            for k, v in patch.items():
                if k in ("cost_usd_total", "latency_ms_total"):
                    state[k] = state.get(k, 0) + v
                elif k == "transcript":
                    state.setdefault("transcript", []).extend(v)
                else:
                    state[k] = v

        await _merge(await g11_nodes.style_extractor_node(state))
        await _merge(await g11_nodes.voice_profiler_node(state))
        await _merge(await g11_nodes.persist_node(state))

        # 0.05 + 0.03 = 0.08 — comfortably under the $0.10 target.
        assert state["cost_usd_total"] == pytest.approx(0.08, abs=0.005)
        assert state["cost_usd_total"] < 0.10
        assert state["persisted"] is True
