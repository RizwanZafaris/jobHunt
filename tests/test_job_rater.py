"""tests/test_job_rater.py — FRD-14 URL Job Rater.

Covers:
  • scoring_agent.score_job_dict returns the same shape as score_role (the
    refactor's contract) — with the 6 dimension scorers mocked.
  • jd_extractor.extract_jd returns nulls for absent fields (no fabrication),
    with the LLM router mocked.
  • POST /jobs/rate-url: jd_text path → rating + rate_token, no jobs insert;
    url-fetch-failure → {needs_jd_text:true}; both-empty → 422.
  • POST /jobs/rate-url/save: inserts once; dedups when a match exists.

All LLM / Apify / Redis / DB seams are mocked — no network, no real services.
"""
import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest


@pytest.fixture
def env(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("SUPABASE_URL", "https://test.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "test-service-key")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "test-service-key")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("RIZWAN_SINGLE_USER_MODE", "1")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "test-anon-key")
    import config.settings as _cs
    _cs._settings = None
    # Disable the slowapi limiter for unit tests: the @limiter.limit decorator
    # otherwise demands a real starlette Request + app.state.limiter. With
    # enabled=False it short-circuits before that check, so we can call the
    # handler bodies directly. Restored after the test.
    from api.rate_limits import limiter
    _prev = limiter.enabled
    limiter.enabled = False
    yield True
    limiter.enabled = _prev


_UID = UUID("00000000-0000-0000-0000-000000000001")
_USER = SimpleNamespace(id=_UID, full_name="Test User", email="t@example.com")


# ─────────────────────────────────────────────────────────────────────
# 1. score_job_dict — refactor contract
# ─────────────────────────────────────────────────────────────────────
def test_score_job_dict_shape_matches_score_role(env, monkeypatch):
    import agents.scoring_agent as sa

    # Mock the 6 dimension scorers + persona/profile loaders (no LLM/DB).
    dim = sa.DimensionScore(score=80, rationale="ok", cost_usd=0.0)

    async def _adim(**k):
        return dim

    def _sdim(**k):
        return dim

    monkeypatch.setattr(sa, "_load_company_persona", lambda *a, **k: {})
    monkeypatch.setattr(sa, "_load_profile_competencies", lambda *a, **k: {})
    monkeypatch.setattr(sa, "_score_role_fit", _adim)
    monkeypatch.setattr(sa, "_score_growth", _adim)
    monkeypatch.setattr(sa, "_score_comp", _adim)
    monkeypatch.setattr(sa, "_score_culture", _adim)
    monkeypatch.setattr(sa, "_score_remote", _sdim)
    monkeypatch.setattr(sa, "_score_trajectory", _adim)

    job = {"title": "Senior PM", "company": "Adyen", "description": "Build payments."}
    out = asyncio.run(sa.score_job_dict(job=job, user_id=_UID))

    # Same keys score_role assembles + surfaces.
    for key in ("composite", "letter_grade", "rationale", "cites",
                "weights", "cost_usd", "scored_at", "scorer_version",
                "role_fit", "growth", "comp", "culture", "remote", "trajectory"):
        assert key in out, f"missing {key}"
    assert isinstance(out["composite"], int)
    assert out["letter_grade"] in {"A", "B", "C", "D", "F"}


# ─────────────────────────────────────────────────────────────────────
# 2. extract_jd — no fabrication
# ─────────────────────────────────────────────────────────────────────
def test_extract_jd_absent_fields_are_null(env, monkeypatch):
    import agents.jd_extractor as jx

    # Router returns a JSON with comp absent → must come back null, not guessed.
    fake = SimpleNamespace(text='{"title":"Staff PM","company":"Stripe",'
                                '"seniority":null,"location":null,"comp_range":null,'
                                '"responsibilities":["ship"],"requirements":["7y"],'
                                '"ats_keywords":["payments","API"]}',
                           cost_usd=0.0)

    class _Router:
        async def ask(self, **k):
            return fake

    monkeypatch.setattr(jx, "get_router", lambda: _Router())

    out = asyncio.run(jx.extract_jd("Staff PM at Stripe. Ship things. 7y exp. Payments, API."))
    assert out["title"] == "Staff PM"
    assert out["company"] == "Stripe"
    assert out["comp_range"] is None        # absent → null, not fabricated
    assert out["seniority"] is None
    assert out["ats_keywords"] == ["payments", "API"]
    assert out["raw_jd_md"]                  # raw text preserved for scoring


def test_extract_jd_empty_input_returns_skeleton(env):
    import agents.jd_extractor as jx
    out = asyncio.run(jx.extract_jd(""))
    assert out["title"] is None
    assert out["ats_keywords"] == []
    assert out["raw_jd_md"] == ""


# ─────────────────────────────────────────────────────────────────────
# 3. /jobs/rate-url endpoint
# ─────────────────────────────────────────────────────────────────────
def _patch_rate_url(monkeypatch, *, fetch=None, extracted=None, breakdown=None, store=None):
    import api.job_rater as jr

    async def _extract(_md):
        return extracted or {"title": "PM", "company": "Acme", "raw_jd_md": _md or "x",
                             "seniority": None, "location": None, "comp_range": None,
                             "responsibilities": [], "requirements": [], "ats_keywords": []}

    async def _score(**k):
        return breakdown or {"composite": 88, "letter_grade": "A"}

    async def _fetch(url):
        return fetch if fetch is not None else ("# JD\nsome text", None)

    monkeypatch.setattr(jr, "_store_rate_token", store or (lambda token, payload: None))
    # patch the lazily-imported names by injecting into the modules they come from
    import agents.jd_extractor as jx
    import agents.scoring_agent as sa
    monkeypatch.setattr(jx, "extract_jd", _extract)
    monkeypatch.setattr(jx, "fetch_jd_from_url", _fetch)
    monkeypatch.setattr(jx, "to_job_dict", lambda e, url=None: {"company": e.get("company"), "description": e.get("raw_jd_md")})
    monkeypatch.setattr(sa, "score_job_dict", _score)


def test_rate_url_jd_text_returns_rating_no_insert(env, monkeypatch):
    import api.job_rater as jr
    stored = {}
    _patch_rate_url(monkeypatch, store=lambda t, p: stored.update({t: p}))

    body = jr.RateUrlBody(jd_text="Senior PM at Acme. Build stuff.")
    out = asyncio.run(jr.rate_url(request=None, body=body, user=_USER))

    assert out["rating"]["letter_grade"] == "A"
    assert "rate_token" in out
    assert out["extracted"]["company"] == "Acme"
    # ephemeral: something was stashed, nothing inserted (no db touched)
    assert stored, "rate_token payload should be stored"


def test_rate_url_fetch_failure_prompts_for_text(env, monkeypatch):
    import api.job_rater as jr
    _patch_rate_url(monkeypatch, fetch=(None, "thin_content"))

    body = jr.RateUrlBody(url="https://jobs.example.com/walled")
    out = asyncio.run(jr.rate_url(request=None, body=body, user=_USER))

    assert out.get("needs_jd_text") is True
    assert out["reason"] == "thin_content"
    assert "rating" not in out


def test_rate_url_both_empty_422(env, monkeypatch):
    import api.job_rater as jr
    from fastapi import HTTPException
    _patch_rate_url(monkeypatch)
    body = jr.RateUrlBody()
    with pytest.raises(HTTPException) as ei:
        asyncio.run(jr.rate_url(request=None, body=body, user=_USER))
    assert ei.value.status_code == 422


# ─────────────────────────────────────────────────────────────────────
# 4. /jobs/rate-url/save endpoint
# ─────────────────────────────────────────────────────────────────────
class _FakeTable:
    def __init__(self, parent):
        self.parent = parent
        self._filters = {}

    def select(self, *a, **k):
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def limit(self, n):
        return self

    def execute(self):
        # dedup lookup
        if self.parent.existing_id is not None:
            return SimpleNamespace(data=[{"id": self.parent.existing_id}])
        return SimpleNamespace(data=[])

    def insert(self, row):
        self.parent.inserted.append(row)
        return SimpleNamespace(execute=lambda: SimpleNamespace(data=[{"id": 4242}]))


class _FakeDB:
    def __init__(self, existing_id=None):
        self.existing_id = existing_id
        self.inserted = []

    def table(self, name):
        return _FakeTable(self)


def _patch_save(monkeypatch, *, payload, db):
    import api.job_rater as jr
    monkeypatch.setattr(jr, "_load_rate_token", lambda t: payload)
    monkeypatch.setattr(jr, "get_supabase", lambda: db)


def test_save_inserts_once(env, monkeypatch):
    import api.job_rater as jr
    payload = {
        "user_id": str(_UID), "url": "https://x.com/job", "source": "manual_url",
        "extracted": {"title": "PM", "company": "Acme", "raw_jd_md": "jd"},
        "breakdown": {"composite": 90, "letter_grade": "A"},
    }
    db = _FakeDB(existing_id=None)
    _patch_save(monkeypatch, payload=payload, db=db)

    out = asyncio.run(jr.save_rated_job(request=None, body=jr.SaveRatedBody(rate_token="t" * 10), user=_USER))
    assert out["deduped"] is False
    assert out["job_id"] == 4242
    assert len(db.inserted) == 1
    assert db.inserted[0]["source"] == "manual_url"
    assert db.inserted[0]["match_score"] == 90


def test_save_dedups_when_existing(env, monkeypatch):
    import api.job_rater as jr
    payload = {
        "user_id": str(_UID), "url": "https://x.com/job", "source": "manual_url",
        "extracted": {"title": "PM", "company": "Acme", "raw_jd_md": "jd"},
        "breakdown": {"composite": 90, "letter_grade": "A"},
    }
    db = _FakeDB(existing_id=999)
    _patch_save(monkeypatch, payload=payload, db=db)

    out = asyncio.run(jr.save_rated_job(request=None, body=jr.SaveRatedBody(rate_token="t" * 10), user=_USER))
    assert out["deduped"] is True
    assert out["job_id"] == 999
    assert len(db.inserted) == 0       # no insert on dedup


def test_save_missing_token_404(env, monkeypatch):
    import api.job_rater as jr
    from fastapi import HTTPException
    monkeypatch.setattr(jr, "_load_rate_token", lambda t: None)
    with pytest.raises(HTTPException) as ei:
        asyncio.run(jr.save_rated_job(request=None, body=jr.SaveRatedBody(rate_token="t" * 10), user=_USER))
    assert ei.value.status_code == 404
