"""
Integration tests for the G7 Application Graph (agents.g7_*).

Verifies:
  - form_scanner extracts fields from mocked Greenhouse HTML
  - question_classifier maps each of the 10 categories correctly
  - answer_retriever pulls from the right source per category
  - persona_critic catches fabricated metrics + banned keywords
  - HITL interrupt pauses the graph; resume returns state
  - form_filler fills fields and stops BEFORE submit
  - persist creates follow_up_cadence row
  - identity_lock rejects fabricated $ metrics
  - Wallet guard refuses stale jobs

No live API calls. Playwright is monkey-patched. DB writes are captured
in an in-memory dict.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.modules.setdefault("supabase", MagicMock())

os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("OPENAI_API_KEY", "test")
os.environ.setdefault("SUPABASE_URL", "http://test")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test")


# ──────────────────────────────────────────────────────────────────────────
# Test fixtures — minimal Greenhouse-shaped HTML
# ──────────────────────────────────────────────────────────────────────────
SAMPLE_GREENHOUSE_HTML = """
<html><head><title>Apply — Senior Product Manager at Acme</title></head>
<body>
<form id="new_job_application" class="application">
  <div class="application--field application--required">
    <label for="job_application_first_name">First name</label>
    <input type="text" id="job_application_first_name" name="job_application[first_name]" required="required"/>
  </div>
  <div class="application--field application--required">
    <label for="job_application_last_name">Last name</label>
    <input type="text" id="job_application_last_name" name="job_application[last_name]" required="required"/>
  </div>
  <div class="application--field application--required">
    <label for="job_application_email">Email</label>
    <input type="email" id="job_application_email" name="job_application[email]" required="required"/>
  </div>
  <div class="application--field">
    <label for="job_application_phone">Phone</label>
    <input type="tel" id="job_application_phone" name="job_application[phone]"/>
  </div>
  <div class="application--field">
    <label for="job_application_linkedin">LinkedIn URL</label>
    <input type="url" id="job_application_linkedin" name="job_application[linkedin]"/>
  </div>
  <div class="application--field">
    <label for="job_application_cover_letter">Cover Letter</label>
    <textarea id="job_application_cover_letter" name="job_application[cover_letter]"></textarea>
  </div>
  <div class="application--field">
    <label for="custom_q_1">Why are you interested in Acme?</label>
    <textarea id="custom_q_1" name="job_application[custom_q_1]"></textarea>
  </div>
  <div class="application--field">
    <label for="custom_q_2">Tell us about a time you shipped a payments product end-to-end.</label>
    <textarea id="custom_q_2" name="job_application[custom_q_2]"></textarea>
  </div>
  <div class="application--field">
    <label for="custom_q_3">What are your salary expectations?</label>
    <input type="text" id="custom_q_3" name="job_application[custom_q_3]"/>
  </div>
  <div class="application--field">
    <label for="custom_q_4">Describe your experience with Python.</label>
    <textarea id="custom_q_4" name="job_application[custom_q_4]"></textarea>
  </div>
  <div class="application--field">
    <label for="custom_q_5">What does diversity, equity, and inclusion mean to you?</label>
    <textarea id="custom_q_5" name="job_application[custom_q_5]"></textarea>
  </div>
  <input type="submit" value="Submit"/>
</form>
</body></html>
"""


class _MockLLMResult:
    def __init__(self, text, cost_usd=0.05, latency_ms=200, provider="anthropic", model="claude-sonnet-4-6"):
        self.text = text
        self.cost_usd = cost_usd
        self.latency_ms = latency_ms
        self.provider = provider
        self.model = model
        self.input_tokens = 100
        self.output_tokens = 50
        self.cache_creation_input_tokens = None
        self.cache_read_input_tokens = None


class _Router:
    """Captures calls + returns deterministic JSON depending on agent_name."""

    def __init__(self, *, fabricate=False, banned=False):
        self.calls: list[dict] = []
        self.fabricate = fabricate
        self.banned = banned

    async def ask(self, **kwargs):
        self.calls.append(kwargs)
        agent = kwargs.get("agent_name", "")

        if agent == "g7.question_classifier":
            # Only "custom_q_1..5" hit the LLM — basic_info / cover_letter /
            # portfolio_url get hit by the heuristic before this call.
            return _MockLLMResult(
                text=(
                    '{"classifications":['
                    '{"idx":6,"field_id":"custom_q_1","classification":"why_this_company","confidence":0.95,"reasoning":"why interested"},'
                    '{"idx":7,"field_id":"custom_q_2","classification":"behavioral","confidence":0.95,"reasoning":"tell us about a time"},'
                    '{"idx":8,"field_id":"custom_q_3","classification":"salary","confidence":0.95,"reasoning":"salary"},'
                    '{"idx":9,"field_id":"custom_q_4","classification":"technical","confidence":0.9,"reasoning":"experience with python"},'
                    '{"idx":10,"field_id":"custom_q_5","classification":"dei_values","confidence":0.95,"reasoning":"DEI"}'
                    ']}'
                ),
                cost_usd=0.05,
            )

        if agent == "g7.answer_generator":
            answer_metric = "$1B TPV" if self.fabricate else "$50M TPV"
            banned_token = "synergy " if self.banned else ""
            return _MockLLMResult(
                text=(
                    '{"answers":['
                    '{"idx":0,"field_id":"job_application_first_name","answer":"Rizwan","cites":[]},'
                    '{"idx":1,"field_id":"job_application_last_name","answer":"Zafar","cites":[]},'
                    '{"idx":2,"field_id":"job_application_email","answer":"rizwan@example.com","cites":[]},'
                    '{"idx":3,"field_id":"job_application_phone","answer":"+15555550100","cites":[]},'
                    '{"idx":4,"field_id":"job_application_linkedin","answer":"https://linkedin.com/in/rizwan","cites":[]},'
                    '{"idx":5,"field_id":"job_application_cover_letter","answer":"Dear hiring manager...","cites":[]},'
                    f'{{"idx":6,"field_id":"custom_q_1","answer":"Acme launched the BNPL integration last week.","cites":[{{"kind":"company_knowledge","id":"ck-1"}}]}},'
                    f'{{"idx":7,"field_id":"custom_q_2","answer":"{banned_token}I led the Daraz BNPL launch, shipping {answer_metric} in 8 months.","cites":[{{"kind":"story_bank","id":"sb-1"}}]}},'
                    '{"idx":8,"field_id":"custom_q_3","answer":"$220k","cites":[{"kind":"comp_cache","id":"cc-1"}]},'
                    '{"idx":9,"field_id":"custom_q_4","answer":"Used Python in scripting and ETL.","cites":[]},'
                    '{"idx":10,"field_id":"custom_q_5","answer":"I sponsored a women-in-payments cohort at SimPaisa.","cites":[]}'
                    ']}'
                ),
                cost_usd=0.15,
            )

        if agent == "g7.persona_critic":
            # The pure-code prescan in persona_critic_node already catches
            # banned / fabricated; we let the LLM accept and the override
            # do its job.
            return _MockLLMResult(
                text=(
                    '{"summary":{"fabricated_metric_count":0,"banned_keyword_hits":[],'
                    '"required_keyword_misses":[],"overall_verdict":"accept"},'
                    '"per_question":[]}'
                ),
                cost_usd=0.05,
            )

        return _MockLLMResult(text="{}", cost_usd=0.0)


class _FakeSupabase:
    """Minimal Supabase client surface used by g7_io."""

    def __init__(self):
        self.captured: dict[str, list[dict]] = {}
        self.rows: dict[str, list[dict]] = {
            "application_answers": [],
            "follow_up_cadence": [],
        }

    def table(self, name):
        return _FakeTable(name, self.captured, self.rows)


class _FakeTable:
    def __init__(self, name, captured, rows):
        self.name = name
        self.captured = captured
        self.rows = rows
        self._filters: dict[str, str] = {}

    def select(self, *_a, **_k):
        return self

    def insert(self, payload):
        self.captured.setdefault(self.name, []).append(payload)
        new_row = dict(payload)
        new_row.setdefault("id", f"fake-{self.name}-{len(self.rows.get(self.name, []))}")
        self.rows.setdefault(self.name, []).append(new_row)

        class _Q:
            data = [new_row]
            def execute(self_inner):
                return self_inner

        return _Q()

    def update(self, payload):
        self._update_payload = payload
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def gte(self, *_a, **_k):
        return self

    def in_(self, *_a, **_k):
        return self

    def order(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def execute(self):
        if hasattr(self, "_update_payload"):
            updated: list[dict] = []
            for r in self.rows.get(self.name, []):
                if all(str(r.get(c)) == str(v) for c, v in self._filters.items()):
                    r.update(self._update_payload)
                    updated.append(r)

            class _R:
                pass
            _R.data = updated
            return _R()
        # select path
        matched = [
            r for r in self.rows.get(self.name, [])
            if all(str(r.get(c)) == str(v) for c, v in self._filters.items())
        ]

        class _R:
            pass
        _R.data = matched
        return _R()


# ══════════════════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════════════════
def test_form_scanner_extracts_fields():
    """Pure-HTML scan path extracts 11 fields from the sample form."""
    from agents.form_scanner import scan_from_html

    result = scan_from_html(SAMPLE_GREENHOUSE_HTML, ats_type="greenhouse", url="https://boards.greenhouse.io/acme/jobs/1")
    assert result.field_count == 11
    # Required flag set on the first three.
    labels = [f.field_label for f in result.fields]
    assert "First name" in labels
    assert "Email" in labels
    # field_type plumbing
    by_label = {f.field_label: f for f in result.fields}
    assert by_label["Email"].field_type == "email"
    assert by_label["Cover Letter"].field_type == "textarea"
    assert by_label["LinkedIn URL"].field_type == "url"


def test_heuristic_classifier_handles_canonical_fields():
    """The heuristic regex pack catches first_name / email / linkedin /
    cover_letter / salary without an LLM call."""
    from agents.form_scanner import scan_from_html
    from agents.g7_nodes import _heuristic_classify
    from agents.form_scanner import load_ats_pattern

    result = scan_from_html(SAMPLE_GREENHOUSE_HTML, ats_type="greenhouse")
    pattern = load_ats_pattern("greenhouse")
    hints = pattern.get("classification_hints", {})

    classified = {}
    for f in result.fields:
        q = f.as_question_dict()
        cat = _heuristic_classify(q, hints)
        classified[q["field_label"]] = cat

    assert classified["First name"] == "basic_info"
    assert classified["Email"] == "basic_info"
    assert classified["LinkedIn URL"] == "portfolio_url"
    assert classified["Cover Letter"] == "cover_letter"
    assert classified["What are your salary expectations?"] == "salary"
    assert classified["What does diversity, equity, and inclusion mean to you?"] == "dei_values"
    # behavioral hint matches
    assert classified["Tell us about a time you shipped a payments product end-to-end."] == "behavioral"


@pytest.mark.asyncio
async def test_question_classifier_maps_all_10_categories(monkeypatch):
    """Heuristic catches 6; LLM batch fills in the remaining 5."""
    from agents import g7_nodes
    from agents.form_scanner import scan_from_html

    result = scan_from_html(SAMPLE_GREENHOUSE_HTML, ats_type="greenhouse")
    questions = [f.as_question_dict() for f in result.fields]

    router = _Router()
    monkeypatch.setattr(g7_nodes, "get_router", lambda: router)

    state = {"questions": questions, "ats_type": "greenhouse"}
    patch = await g7_nodes.question_classifier_node(state)

    cats = {q["field_label"]: q["classification"] for q in patch["questions"]}
    assert cats["First name"] == "basic_info"
    assert cats["Email"] == "basic_info"
    assert cats["Cover Letter"] == "cover_letter"
    assert cats["LinkedIn URL"] == "portfolio_url"
    assert cats["Why are you interested in Acme?"] == "why_this_company"
    assert cats["Tell us about a time you shipped a payments product end-to-end."] == "behavioral"
    assert cats["What are your salary expectations?"] == "salary"
    assert cats["Describe your experience with Python."] == "technical"
    assert cats["What does diversity, equity, and inclusion mean to you?"] == "dei_values"


@pytest.mark.asyncio
async def test_answer_retriever_routes_by_category(monkeypatch):
    """behavioral → story_bank; why_this_company → company_knowledge;
    salary → comp_band; basic_info → profile."""
    from agents import g7_nodes
    import db.client as dbc
    import agents.comp_research as cr

    async def _fake_story_bank(topic, match_count=3):
        return [{"id": "sb-1", "title": "Daraz BNPL launch", "source_text": "$50M TPV in 8 months."}]

    async def _fake_company_knowledge(*, company_name, query, match_count=5):
        return [{"id": "ck-1", "section": "news", "content": "Acme launched BNPL."}]

    async def _fake_get_comp_band(*, company, role, level=None, location=None, user_id):
        from agents.comp_research import CompBand
        return CompBand(
            company="Acme", role="PM", level=None, location="Remote",
            currency="USD",
            p25=180_000, p50=200_000, p75=230_000, p90=260_000,
            source_summary="test", source_citations=[],
            cached=True, age_days=0, cost_usd=0.0,
            confidence="high", cache_id="cc-1",
        )

    monkeypatch.setattr(dbc, "search_story_bank", _fake_story_bank, raising=False)
    monkeypatch.setattr(dbc, "search_company_knowledge", _fake_company_knowledge, raising=False)
    monkeypatch.setattr(cr, "get_comp_band", _fake_get_comp_band, raising=False)

    state = {
        "user_id": "u1",
        "application_row": {"company": "Acme", "role": "PM", "id": 1},
        "company_persona": {"id": "p1", "success_patterns": ["ship fast"]},
        "profile": {"full_name": "Rizwan Zafar", "email": "rizwan@example.com"},
        "questions": [
            {"idx": 0, "classification": "basic_info", "field_label": "First name", "field_id": "x"},
            {"idx": 1, "classification": "behavioral", "field_label": "Tell us about a time...", "field_id": "y"},
            {"idx": 2, "classification": "why_this_company", "field_label": "Why Acme?", "field_id": "z"},
            {"idx": 3, "classification": "salary", "field_label": "Expected comp?", "field_id": "s"},
            {"idx": 4, "classification": "dei_values", "field_label": "DEI?", "field_id": "d"},
        ],
    }
    patch = await g7_nodes.answer_retriever_node(state)
    qs = {q["idx"]: q for q in patch["questions"]}
    assert any(c["kind"] == "profile" for c in qs[0]["retrieved_context"])
    assert any(c["kind"] == "story_bank" for c in qs[1]["retrieved_context"])
    assert qs[1]["cites"][0]["kind"] == "story_bank"
    assert any(c["kind"] == "company_knowledge" for c in qs[2]["retrieved_context"])
    assert qs[2]["cites"][0]["kind"] == "company_knowledge"
    assert any(c["kind"] == "comp_band" for c in qs[3]["retrieved_context"])
    assert any(c["kind"] == "company_persona" for c in qs[4]["retrieved_context"])


def test_persona_critic_catches_fabricated_metrics():
    """The pure-code prescan inside persona_critic detects $-metrics not
    found in retrieved_context or cv.md."""
    from agents.g7_nodes import _scan_fabricated_metrics

    cv = "Led Daraz BNPL: $50M TPV in 8 months."
    # Honest metric: matches cv
    fabs = _scan_fabricated_metrics(
        answer="I shipped the Daraz BNPL launch ($50M TPV).",
        retrieved_context=[],
        cv_md=cv,
    )
    assert fabs == []

    # Inflated metric: NOT in cv → flagged.
    fabs = _scan_fabricated_metrics(
        answer="I shipped the Daraz BNPL launch ($1B TPV).",
        retrieved_context=[],
        cv_md=cv,
    )
    assert "$1B" in fabs or any("$1B" in f for f in fabs)

    # Multiplier from context — present
    fabs = _scan_fabricated_metrics(
        answer="3x conversion lift",
        retrieved_context=[{"content": "Drove a 3x conversion lift on checkout."}],
        cv_md="",
    )
    assert fabs == []


@pytest.mark.asyncio
async def test_persona_critic_banned_keyword_override(monkeypatch):
    """Even if the LLM says accept, prescan-found banned keywords force
    verdict=revise."""
    from agents import g7_nodes

    router = _Router()
    monkeypatch.setattr(g7_nodes, "get_router", lambda: router)

    state = {
        "questions": [
            {"idx": 0, "classification": "behavioral",
             "field_label": "STAR question",
             "generated_answer": "I drove synergy across teams while shipping the launch.",
             "retrieved_context": []},
        ],
        "company_persona": {"ats_keyword_bank": {"banned": ["synergy"]}},
        "cv_md": "",
    }
    patch = await g7_nodes.persona_critic_node(state)
    crit = patch["questions"][0]["persona_critique"]
    assert crit["verdict"] == "revise"
    assert "synergy" in crit["banned_keywords_in_answer"]


@pytest.mark.asyncio
async def test_form_filler_stops_before_submit(monkeypatch):
    """form_filler must NEVER call page.click on a submit selector."""
    from agents import form_scanner

    fill_calls: list[dict] = []

    async def _fake_fill_form(url, answers, **kw):
        # We assert skip_submit=True here.
        assert kw.get("skip_submit") is True
        fill_calls.append({"url": url, "answers": answers, "kw": kw})
        return {
            "filled": len(answers),
            "failed": 0,
            "errors": [],
            "stopped_before_submit": True,
        }

    monkeypatch.setattr(form_scanner, "fill_form", _fake_fill_form, raising=False)

    from agents import g7_nodes
    monkeypatch.setattr(g7_nodes, "fill_form", _fake_fill_form, raising=False)
    state = {
        "form_url": "https://boards.greenhouse.io/acme/jobs/1",
        "ats_type": "greenhouse",
        "questions": [
            {"idx": 0, "field_id": "x", "field_type": "text",
             "generated_answer": "Rizwan", "approved": True, "user_edited": None,
             "options": None},
        ],
    }
    patch = await g7_nodes.form_filler_node(state)
    assert patch["stopped_before_submit"] is True
    assert patch["filler_summary"]["filled"] == 1


def test_form_filler_refuses_skip_submit_false():
    """The skip_submit=False call path is hard-coded to raise — the HITL
    invariant must be impossible to disable."""
    import asyncio
    from agents.form_scanner import fill_form

    async def _run():
        return await fill_form("http://x", [], skip_submit=False)

    with pytest.raises(RuntimeError, match="skip_submit"):
        asyncio.run(_run())


@pytest.mark.asyncio
async def test_persist_creates_follow_up_cadence_row(monkeypatch):
    """The persist node should INSERT a follow_up_cadence row when none
    exists for this application."""
    fake_db = _FakeSupabase()
    from agents import g7_io
    from db import client as dbc
    monkeypatch.setattr(dbc, "get_supabase", lambda: fake_db, raising=False)
    monkeypatch.setattr(g7_io, "load_application", lambda app_id: {"id": app_id, "company": "Acme"}, raising=False)

    # Seed an application_answers row.
    fake_db.rows["application_answers"].append({
        "id": "aa-1", "user_id": "u1", "application_id": 99,
        "questions": [], "status": "drafting",
    })

    from agents import g7_nodes
    state = {
        "application_answer_id": "aa-1",
        "user_id": "u1",
        "application_id": 99,
        "questions": [
            {"idx": 0, "generated_answer": "hello", "user_edited": None,
             "retrieved_context": []},
        ],
        "cost_usd_total": 0.20,
    }
    patch = await g7_nodes.persist_node(state)
    assert patch.get("cadence_row_id")
    # A row was inserted
    assert len(fake_db.rows["follow_up_cadence"]) == 1
    row = fake_db.rows["follow_up_cadence"][0]
    assert row["application_id"] == 99
    assert row["current_status"] == "applied"


@pytest.mark.asyncio
async def test_identity_lock_redacts_fabricated_metrics(monkeypatch):
    """persist node must redact $ metrics not found in source."""
    fake_db = _FakeSupabase()
    fake_db.rows["application_answers"].append({
        "id": "aa-2", "user_id": "u1", "application_id": 100,
        "questions": [], "status": "drafting",
    })

    from agents import g7_io, g7_nodes
    from db import client as dbc
    monkeypatch.setattr(dbc, "get_supabase", lambda: fake_db, raising=False)
    monkeypatch.setattr(g7_io, "load_application", lambda app_id: {"id": app_id, "company": "Acme"}, raising=False)

    state = {
        "application_answer_id": "aa-2",
        "user_id": "u1",
        "application_id": 100,
        "cv_md": "Daraz BNPL: $50M TPV in 8 months.",
        "questions": [
            {"idx": 0, "generated_answer": "I shipped Daraz BNPL ($999M TPV).",
             "user_edited": None,
             "retrieved_context": []},
        ],
        "cost_usd_total": 0.20,
    }
    patch = await g7_nodes.persist_node(state)
    redactions = patch["identity_lock_redactions"]
    assert len(redactions) >= 1
    assert any("$999M" in r["metric"] or "999" in r["metric"] for r in redactions)
    # The generated answer should now contain "[redacted]" in place of $999M.
    final_q = patch["questions"][0]
    assert "[redacted]" in (final_q.get("generated_answer") or "")


@pytest.mark.asyncio
async def test_hitl_interrupt_pauses_then_resumes(monkeypatch):
    """The graph must pause at human_review and resume cleanly with
    the user_approvals payload merged into questions."""
    pytest.importorskip("langgraph")
    pytest.importorskip("langgraph.checkpoint.memory")

    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.types import Command

    fake_db = _FakeSupabase()
    fake_db.rows["application_answers"].append({
        "id": "aa-hitl", "user_id": "u1", "application_id": 7,
        "questions": [], "status": "drafting",
    })

    from agents import g7_io, g7_nodes
    from agents.g7_graph import build_g7_graph
    from db import client as dbc
    monkeypatch.setattr(dbc, "get_supabase", lambda: fake_db, raising=False)
    monkeypatch.setattr(g7_io, "load_application", lambda app_id: {"id": app_id, "company": "Acme"}, raising=False)

    # Mock fill_form to skip the real Playwright call.
    async def _fake_fill_form(url, answers, **kw):
        assert kw.get("skip_submit") is True
        return {"filled": len(answers), "failed": 0, "errors": [], "stopped_before_submit": True}
    monkeypatch.setattr(g7_nodes, "fill_form", _fake_fill_form, raising=False)

    # Pre-supply questions so we can shortcut the form_scanner Playwright
    # call by passing _test_html to form_scanner_node via state.
    router = _Router()
    monkeypatch.setattr(g7_nodes, "get_router", lambda: router)

    # Stub the retrieval helpers so the graph runs without hitting real DB.
    import db.client as dbc2
    async def _ret(*a, **k): return []
    monkeypatch.setattr(dbc2, "search_story_bank", _ret, raising=False)
    monkeypatch.setattr(dbc2, "search_company_knowledge", _ret, raising=False)

    initial_state = {
        "application_answer_id": "aa-hitl",
        "application_id": 7,
        "user_id": "u1",
        "form_url": "https://boards.greenhouse.io/acme/jobs/1",
        "ats_type": "greenhouse",
        "application_row": {"id": 7, "company": "Acme", "role": "PM"},
        "job_row": None,
        "company_persona": None,
        "cv_md": "Daraz BNPL: $50M TPV in 8 months.",
        "profile": {"full_name": "Rizwan Zafar", "email": "r@example.com"},
        "questions": [],
        "generation_iteration": 0,
        "generation_attempts": 0,
        "cost_usd_total": 0.0,
        "latency_ms_total": 0,
        "transcript": [],
        "_test_html": SAMPLE_GREENHOUSE_HTML,
    }

    memory = MemorySaver()
    graph = build_g7_graph(checkpointer=memory)
    config = {"configurable": {"thread_id": "aa-hitl"}}

    # First invoke — should pause at human_review.
    partial = await graph.ainvoke(initial_state, config=config)

    # The interrupt() means the graph hasn't completed. status should be
    # awaiting_review on disk; the in-flight state shows questions + draft.
    assert (partial or {}).get("status") != "filled"
    row = next(r for r in fake_db.rows["application_answers"] if r["id"] == "aa-hitl")
    assert row["status"] == "awaiting_review"
    questions_after_pause = row["questions"]
    assert len(questions_after_pause) == 11

    # Resume with user_approvals — approve them all.
    approvals = [
        {"idx": q["idx"], "approved": True, "user_edited": None}
        for q in questions_after_pause
    ]
    final = await graph.ainvoke(Command(resume={"approvals": approvals}), config=config)

    # The graph should have run form_filler + persist.
    assert any(t["node"] == "form_filler" for t in final["transcript"])
    assert any(t["node"] == "persist" for t in final["transcript"])
    # And a cadence row should have been created.
    assert len(fake_db.rows["follow_up_cadence"]) == 1


@pytest.mark.asyncio
async def test_wallet_guard_refuses_stale_jobs(monkeypatch):
    """start_g7_run must refuse when the job is closed (and force=False)."""
    fake_db = _FakeSupabase()

    from agents import g7_io
    from db import client as dbc
    monkeypatch.setattr(dbc, "get_supabase", lambda: fake_db, raising=False)
    monkeypatch.setattr(
        g7_io, "load_application",
        lambda app_id: {
            "id": app_id, "company": "Acme", "role": "PM", "job_id": 555,
            "user_id": "00000000-0000-0000-0000-000000000001",
        },
        raising=False,
    )

    # Make load_open_job_or_none return None (stale job).
    import api._job_guards as jg
    monkeypatch.setattr(jg, "load_open_job_or_none", lambda *a, **k: None, raising=False)

    with pytest.raises(PermissionError, match="job_closed_or_stale"):
        await g7_io.start_g7_run(
            application_id=1,
            form_url="https://boards.greenhouse.io/acme/jobs/1",
            user_id="00000000-0000-0000-0000-000000000001",
            force=False,
        )
