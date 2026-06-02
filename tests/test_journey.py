"""tests/test_journey.py — FRD-16 High-Fit Auto-Prep Journey.

Covers the orchestrator contract (api/journey.py):
  - trigger boundary: composite >=90 fires, <90 / disabled / None does not;
  - create_journey_for_job: skip closed/invalid, dedup, daily cap, draft-app
    creation, three-leg fan-out, and one-leg-fails isolation;
  - aggregate status rollup.

Style mirrors tests/test_resume_build_resilience.py: supabase is stubbed and we
patch at api.journey / api.queue seams with a tiny routing fake DB.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

sys.modules.setdefault("supabase", MagicMock())

import api.journey as J  # noqa: E402

UID = "11111111-1111-1111-1111-111111111111"


# ── tiny routing fake DB ────────────────────────────────────────────────────
class _Resp:
    def __init__(self, data=None, count=None):
        self.data = data if data is not None else []
        self.count = count


class _Chain:
    """Every query-builder method returns self; execute() yields the scripted
    response for this table call."""
    def __init__(self, resp):
        self._resp = resp

    def select(self, *a, **k):
        return self

    def insert(self, *a, **k):
        return self

    def update(self, *a, **k):
        return self

    def eq(self, *a, **k):
        return self

    def gte(self, *a, **k):
        return self

    def is_(self, *a, **k):
        return self

    def order(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def execute(self):
        return self._resp


class _FakeDB:
    """Returns scripted responses per table, consumed in call order."""
    def __init__(self, scripts: dict):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls: list[str] = []

    def table(self, name):
        self.calls.append(name)
        seq = self.scripts.get(name) or [_Resp([])]
        resp = seq.pop(0) if len(seq) > 1 else seq[0]
        return _Chain(resp)


def _open_job(job_id=13908, company="Adyen"):
    return {
        "id": job_id, "company": company, "title": "Head of Product",
        "company_id": "co-1", "posting_closed_at": None,
        "validation_failed": None,
        "fit_score_breakdown": {"composite": 95}, "match_score": 95,
    }


def _patch_db(fake):
    return patch("db.client.get_supabase", return_value=fake)


def _patch_enqueues(g2="r-g2", g3="r-g3", ppl="r-ppl"):
    """Patch the three leg enqueues api.journey imports from api.queue."""
    return (
        patch("api.queue.enqueue_g2_build", return_value=g2),
        patch("api.queue.enqueue_g3_interview_prep", return_value=g3),
        patch("api.queue.enqueue_people_finder", return_value=ppl),
    )


# ── 1. aggregate rollup (pure) ──────────────────────────────────────────────
def _legs(*statuses):
    out, names = {}, ["resume", "prep", "network"]
    return [{"run_id": (f"r{i}" if s else None), "status": s or "not_started"}
            for i, s in enumerate(statuses)]


def test_aggregate_all_succeeded_is_converged():
    assert J._aggregate(_legs("succeeded", "succeeded", "succeeded")) == "converged"


def test_aggregate_mixed_terminal_is_partial():
    assert J._aggregate(_legs("succeeded", "failed", "succeeded")) == "partial"


def test_aggregate_all_failed_is_failed():
    assert J._aggregate(_legs("failed", "failed", "failed")) == "failed"


def test_aggregate_any_running_is_running():
    assert J._aggregate(_legs("succeeded", "running", "succeeded")) == "running"


# ── 2. trigger boundary ─────────────────────────────────────────────────────
def test_trigger_fires_at_threshold():
    with patch.object(J, "_cfg", return_value=(True, 90, 8)), \
            patch("api.queue.enqueue_journey", return_value="run-1") as enq:
        out = J.maybe_trigger_journey(user_id=UID, job_id=1, composite=90)
    assert out == "run-1"
    enq.assert_called_once()


def test_trigger_skips_below_threshold():
    with patch.object(J, "_cfg", return_value=(True, 90, 8)), \
            patch("api.queue.enqueue_journey") as enq:
        out = J.maybe_trigger_journey(user_id=UID, job_id=1, composite=89)
    assert out is None
    enq.assert_not_called()


def test_trigger_skips_when_disabled():
    with patch.object(J, "_cfg", return_value=(False, 90, 8)), \
            patch("api.queue.enqueue_journey") as enq:
        assert J.maybe_trigger_journey(user_id=UID, job_id=1, composite=99) is None
    enq.assert_not_called()


def test_trigger_failopen_on_enqueue_error():
    with patch.object(J, "_cfg", return_value=(True, 90, 8)), \
            patch("api.queue.enqueue_journey", side_effect=RuntimeError("redis down")):
        # Must NOT raise — scoring can never be broken by the journey trigger.
        assert J.maybe_trigger_journey(user_id=UID, job_id=1, composite=95) is None


# ── 3. create_journey_for_job ───────────────────────────────────────────────
def test_create_skips_closed_posting():
    closed = _open_job()
    closed["posting_closed_at"] = "2026-05-01T00:00:00Z"
    fake = _FakeDB({"jobs": [_Resp([closed])]})
    with _patch_db(fake), patch.object(J, "_cfg", return_value=(True, 90, 8)):
        out = J.create_journey_for_job(user_id=UID, job_id=13908)
    assert out["skipped"] and out["reason"] == "posting_closed_or_invalid"


def test_create_dedup_when_journey_exists():
    fake = _FakeDB({
        "jobs": [_Resp([_open_job()])],
        "journeys": [_Resp([{"id": "existing"}])],  # dedup hit
    })
    with _patch_db(fake), patch.object(J, "_cfg", return_value=(True, 90, 8)):
        out = J.create_journey_for_job(user_id=UID, job_id=13908)
    assert out["skipped"] and out["reason"] == "already_journeyed"


def test_create_respects_daily_cap():
    fake = _FakeDB({
        "jobs": [_Resp([_open_job()])],
        "journeys": [_Resp([]), _Resp([], count=8)],  # dedup empty, cap count=8
    })
    with _patch_db(fake), patch.object(J, "_cfg", return_value=(True, 90, 8)):
        out = J.create_journey_for_job(user_id=UID, job_id=13908)
    assert out["skipped"] and out["reason"] == "daily_cap"


def test_create_happy_path_fans_out_three_legs():
    fake = _FakeDB({
        "jobs": [_Resp([_open_job()])],
        "journeys": [_Resp([]), _Resp([], count=0),
                     _Resp([{"id": "jrny-1"}]), _Resp([{}])],
        "applications": [_Resp([]), _Resp([{"id": "app-1"}])],  # none, then insert
    })
    p_g2, p_g3, p_ppl = _patch_enqueues()
    with _patch_db(fake), patch.object(J, "_cfg", return_value=(True, 90, 8)), \
            p_g2 as g2, p_g3 as g3, p_ppl as ppl:
        out = J.create_journey_for_job(user_id=UID, job_id=13908)

    assert out["journey_id"] == "jrny-1"
    assert out["application_id"] == "app-1"
    assert out["resume_run_id"] == "r-g2"
    assert out["prep_run_id"] == "r-g3"
    assert out["network_run_id"] == "r-ppl"
    assert out["status"] == "running"
    g2.assert_called_once()
    # G3 attaches to the auto-created draft application.
    assert g3.call_args.kwargs.get("application_id") == "app-1"
    # network sweep targets the job's company.
    assert ppl.call_args.kwargs.get("company_name") == "Adyen"


def test_create_one_leg_failure_is_isolated():
    fake = _FakeDB({
        "jobs": [_Resp([_open_job()])],
        "journeys": [_Resp([]), _Resp([], count=0),
                     _Resp([{"id": "jrny-1"}]), _Resp([{}])],
        "applications": [_Resp([]), _Resp([{"id": "app-1"}])],
    })
    _p_g2, p_g3, p_ppl = _patch_enqueues()
    with _patch_db(fake), patch.object(J, "_cfg", return_value=(True, 90, 8)), \
            patch("api.queue.enqueue_g2_build", side_effect=RuntimeError("boom")), \
            p_g3, p_ppl:
        out = J.create_journey_for_job(user_id=UID, job_id=13908)

    # Resume leg failed to enqueue, but prep + network still fired.
    assert out["resume_run_id"] is None
    assert out["prep_run_id"] == "r-g3"
    assert out["network_run_id"] == "r-ppl"
    assert "resume" in out["leg_errors"]
    assert out["status"] == "running"  # other legs present → not dead
