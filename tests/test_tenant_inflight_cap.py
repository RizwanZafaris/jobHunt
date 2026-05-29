"""tests/test_tenant_inflight_cap.py — per-tenant in-flight job cap (P2-3).

Covers the Redis-backed per-(user_id, queue_class) in-flight cap that gives
jobHunt per-tenant fairness (docs/SCALABILITY_BUILD_PLAN.md P2-3): one
tenant's batch must not starve everyone on the shared single-FIFO queue.

The contract under test (mirrors api/queue.py):
  - under-cap enqueues succeed and INCR the counter;
  - an at/over-cap NEW enqueue is refused with ``TenantQueueFull``;
  - a dedup/idempotency hit while at the cap STILL returns the existing run
    id — never refused, never counted (dedup wins over the cap);
  - the worker DECR (``release_inflight``) frees a slot, never goes negative;
  - any Redis error on the counter path FAILS OPEN (enqueue allowed);
  - cap <= 0 (or unset) DISABLES the cap entirely (full back-compat).

Style mirrors tests/test_redis_circuit_breaker.py: the fail-open / disabled
tests need no Redis and always run; the counter-mechanics tests run against a
tiny in-memory fake Redis (so they run in CI without the ``fakeredis`` dep),
and a parallel ``fakeredis``-gated test asserts parity with a real Redis
double when it's installed.
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# api.queue imports lazily, but jobs_runs / agents pull in supabase
# transitively on some paths; stub it like the sibling tests do so import
# never fails in a bare env.
sys.modules.setdefault("supabase", MagicMock())

import api.queue as q  # noqa: E402
from api.queue import TenantQueueFull  # noqa: E402

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"


# ─── A tiny in-memory Redis double (only the ops api/queue.py uses) ──────────
class _FakeRedis:
    """Just enough Redis for the counter path: incr/decr/expire/get/set +
    a pipeline that batches incr+expire. Values are stored as the bytes a
    real ``decode_responses=False`` connection returns, to exercise the
    int-coercion in api/queue.py."""

    def __init__(self):
        self.store: dict[str, int] = {}

    # — plain ops —
    def incr(self, key):
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def decr(self, key):
        self.store[key] = self.store.get(key, 0) - 1
        return self.store[key]

    def expire(self, key, ttl):
        return True

    def get(self, key):
        if key not in self.store:
            return None
        return str(self.store[key]).encode()  # bytes, like the real conn

    def set(self, key, val):
        self.store[key] = int(val)
        return True

    # — pipeline —
    def pipeline(self):
        return _FakePipe(self)


class _FakePipe:
    def __init__(self, parent: _FakeRedis):
        self.parent = parent
        self._ops = []

    def incr(self, key):
        self._ops.append(("incr", key))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    def execute(self):
        out = []
        for op in self._ops:
            if op[0] == "incr":
                out.append(self.parent.incr(op[1]))
            elif op[0] == "expire":
                out.append(self.parent.expire(op[1], op[2]))
        self._ops = []
        return out


def _exploding_redis():
    """A Redis stand-in whose every method raises — simulates an outage."""
    boom = MagicMock()
    err = ConnectionError("redis is down")
    for method in ("incr", "decr", "expire", "get", "set", "pipeline", "ping"):
        getattr(boom, method).side_effect = err
    return boom


@pytest.fixture
def fake_redis():
    """Wire a fresh in-memory fake into api.queue._get_redis (the singleton
    the cap helpers reuse) and reset the cap to a small, ENABLED value."""
    server = _FakeRedis()
    with patch.object(q, "_get_redis", return_value=server), \
            patch.object(q, "_inflight_cap", return_value=3):
        yield server


# ─── under-cap enqueues succeed (counter increments) ─────────────────────────
def test_under_cap_allows_and_increments(fake_redis):
    # 0 -> not over the cap of 3.
    over, in_flight, cap = q._over_inflight_cap(USER_A, "interactive")
    assert over is False
    assert (in_flight, cap) == (0, 3)

    # Two fresh enqueues take us to 2/3 — still under.
    assert q.incr_inflight(USER_A, "interactive") == 1
    assert q.incr_inflight(USER_A, "interactive") == 2
    over, in_flight, cap = q._over_inflight_cap(USER_A, "interactive")
    assert over is False
    assert in_flight == 2


# ─── at-cap enqueue is refused (TenantQueueFull) ─────────────────────────────
def test_at_cap_is_over(fake_redis):
    q.incr_inflight(USER_A, "interactive")
    q.incr_inflight(USER_A, "interactive")
    q.incr_inflight(USER_A, "interactive")  # 3/3 == cap
    over, in_flight, cap = q._over_inflight_cap(USER_A, "interactive")
    assert over is True
    assert (in_flight, cap) == (3, 3)


def test_enqueue_or_dedup_refuses_at_cap(fake_redis):
    """End-to-end through _enqueue_or_dedup: with no dedup hit and the
    tenant at the cap, a NEW enqueue raises TenantQueueFull *before* any
    jobs_runs row is created or any job is enqueued."""
    # Saturate the tenant's interactive class.
    for _ in range(3):
        q.incr_inflight(USER_A, "interactive")

    create_run = MagicMock()
    with patch("api.jobs_runs.find_by_idempotency_key", return_value=None), \
            patch("api.jobs_runs.create_run", create_run):
        with pytest.raises(TenantQueueFull) as ei:
            q._enqueue_or_dedup(
                user_id=USER_A,
                kind="g2_resume",          # → 'interactive'
                payload={"job_id": 1},
                worker_func="api.worker.worker_run_g2",
            )
    # Refused before doing any work.
    create_run.assert_not_called()
    err = ei.value
    assert err.queue_class == "interactive"
    assert err.cap == 3
    assert err.in_flight == 3


def test_tenants_are_isolated(fake_redis):
    """One tenant at the cap must not affect another tenant."""
    for _ in range(3):
        q.incr_inflight(USER_A, "interactive")
    over_a, _, _ = q._over_inflight_cap(USER_A, "interactive")
    over_b, in_b, _ = q._over_inflight_cap(USER_B, "interactive")
    assert over_a is True
    assert over_b is False
    assert in_b == 0


def test_queue_classes_are_isolated(fake_redis):
    """The cap is per (user, queue_class): saturating 'interactive' must
    not refuse a 'batch' job for the same tenant."""
    for _ in range(3):
        q.incr_inflight(USER_A, "interactive")
    over_batch, in_batch, _ = q._over_inflight_cap(USER_A, "batch")
    assert over_batch is False
    assert in_batch == 0


# ─── dedup hit while at cap STILL returns existing id (not refused) ──────────
def test_active_dedup_hit_bypasses_cap(fake_redis):
    """A user re-clicking while at the cap must get their existing run id,
    never a 429. The dedup branch returns before the cap check."""
    for _ in range(3):
        q.incr_inflight(USER_A, "interactive")  # at cap

    existing = MagicMock()
    existing.id = "existing-run-123"
    existing.status = "running"  # ACTIVE_STATUSES

    create_run = MagicMock()
    with patch("api.jobs_runs.find_by_idempotency_key", return_value=existing), \
            patch("api.jobs_runs.create_run", create_run):
        run_id = q._enqueue_or_dedup(
            user_id=USER_A,
            kind="g2_resume",
            payload={"job_id": 7},
            worker_func="api.worker.worker_run_g2",
        )
    assert run_id == "existing-run-123"
    create_run.assert_not_called()  # dedup short-circuited before the cap


def test_terminal_dedup_hit_bypasses_cap(fake_redis):
    """A terminal dedup hit (succeeded/failed/cancelled) also returns the
    existing id and is never refused — it isn't a new in-flight job."""
    for _ in range(3):
        q.incr_inflight(USER_A, "interactive")  # at cap

    existing = MagicMock()
    existing.id = "terminal-run-999"
    existing.status = "succeeded"  # TERMINAL_STATUSES

    with patch("api.jobs_runs.find_by_idempotency_key", return_value=existing), \
            patch("api.jobs_runs.create_run", MagicMock()):
        run_id = q._enqueue_or_dedup(
            user_id=USER_A,
            kind="g2_resume",
            payload={"job_id": 7},
            worker_func="api.worker.worker_run_g2",
        )
    assert run_id == "terminal-run-999"


# ─── decrement on completion frees a slot ────────────────────────────────────
def test_decrement_frees_a_slot(fake_redis):
    for _ in range(3):
        q.incr_inflight(USER_A, "interactive")
    assert q._over_inflight_cap(USER_A, "interactive")[0] is True

    # Worker finishes one job → release one slot.
    q.release_inflight(USER_A, "g2_resume")  # g2_resume → 'interactive'
    over, in_flight, _ = q._over_inflight_cap(USER_A, "interactive")
    assert over is False
    assert in_flight == 2


def test_decrement_never_goes_negative(fake_redis):
    """A stray decrement (or one after the TTL already reaped the key) must
    clamp at 0, never start over-crediting future enqueues."""
    # Counter is at 0; decrement anyway.
    q.decr_inflight(USER_A, "batch")
    val = fake_redis.store.get(q._inflight_key(USER_A, "batch"))
    assert val == 0  # clamped, not -1

    # And the observed in-flight is 0, so the next enqueue is allowed.
    over, in_flight, _ = q._over_inflight_cap(USER_A, "batch")
    assert over is False
    assert in_flight == 0


def test_release_maps_kind_to_class(fake_redis):
    """release_inflight must decrement the SAME class the kind enqueues to,
    so INCR/DECR pair up. g5_score → 'batch'."""
    q.incr_inflight(USER_A, "batch")
    q.incr_inflight(USER_A, "batch")
    q.release_inflight(USER_A, "g5_score")  # → 'batch'
    _, in_flight, _ = q._over_inflight_cap(USER_A, "batch")
    assert in_flight == 1
    assert q.queue_class_for_kind("g5_score") == "batch"
    assert q.queue_class_for_kind("g2_resume") == "interactive"
    assert q.queue_class_for_kind("unknown_kind") == q._DEFAULT_QUEUE_CLASS


# ─── Redis-error path FAILS OPEN (enqueue allowed) ───────────────────────────
def test_over_cap_fails_open_on_redis_error():
    """If the counter read errors, _over_inflight_cap must report not-over
    (allow the enqueue). A counter blip must never block real work."""
    with patch.object(q, "_get_redis", return_value=_exploding_redis()), \
            patch.object(q, "_inflight_cap", return_value=3):
        over, in_flight, cap = q._over_inflight_cap(USER_A, "interactive")
    assert over is False  # fail-open
    assert cap == 3


def test_incr_fails_open_returns_none():
    """INCR errors must be swallowed and return None — the enqueue proceeds."""
    with patch.object(q, "_get_redis", return_value=_exploding_redis()):
        assert q.incr_inflight(USER_A, "interactive") is None


def test_decr_never_raises_on_redis_error():
    """DECR sits in the worker terminal path — it must swallow Redis errors."""
    with patch.object(q, "_get_redis", return_value=_exploding_redis()):
        q.decr_inflight(USER_A, "interactive")  # must not raise


def test_enqueue_proceeds_when_counter_redis_errors():
    """Full fail-open: with the cap enabled but the counter's Redis ops
    broken, _enqueue_or_dedup must NOT refuse — it creates the run, enqueues
    it, and returns its id. We isolate the *counter* failure from the queue's
    own Redis path by giving the queue a healthy mock, so this proves the cap
    check itself fails open (over=False) rather than 429-ing the tenant."""
    run = MagicMock()
    run.id = "run-failopen"

    # The counter helpers see an exploding Redis (GET/INCR raise → fail open),
    # but the RQ enqueue is mocked healthy so we test ONLY the cap's fail-open.
    healthy_queue = MagicMock()
    healthy_queue.name = "jobhunt"
    with patch.object(q, "_get_redis", return_value=_exploding_redis()), \
            patch.object(q, "_inflight_cap", return_value=3), \
            patch.object(q, "_get_queue", return_value=healthy_queue), \
            patch("api.jobs_runs.find_by_idempotency_key", return_value=None), \
            patch("api.jobs_runs.create_run", return_value=run):
        run_id = q._enqueue_or_dedup(
            user_id=USER_A,
            kind="g2_resume",
            payload={"job_id": 99},
            worker_func="api.worker.worker_run_g2",
        )
    # The tenant was NEVER refused despite the broken counter, and the job
    # was enqueued normally.
    assert run_id == "run-failopen"
    healthy_queue.enqueue.assert_called_once()


# ─── cap <= 0 / unset DISABLES the cap entirely ──────────────────────────────
def test_cap_disabled_never_refuses():
    """With the cap <= 0 the feature is OFF: _over_inflight_cap always says
    not-over and never touches Redis."""
    redis_spy = MagicMock()
    with patch.object(q, "_inflight_cap", return_value=0), \
            patch.object(q, "_get_redis", return_value=redis_spy):
        over, in_flight, cap = q._over_inflight_cap(USER_A, "interactive")
    assert over is False
    assert cap == 0
    redis_spy.get.assert_not_called()  # short-circuited; no Redis traffic


def test_cap_disabled_release_is_noop():
    """release_inflight short-circuits when the cap is disabled — no Redis."""
    redis_spy = MagicMock()
    with patch.object(q, "_inflight_cap", return_value=0), \
            patch.object(q, "_get_redis", return_value=redis_spy):
        q.release_inflight(USER_A, "g2_resume")
    redis_spy.decr.assert_not_called()


def test_cap_disabled_enqueue_skips_incr():
    """With the cap disabled, a fresh enqueue must not INCR the counter."""
    run = MagicMock()
    run.id = "run-nocap"
    with patch.object(q, "_inflight_cap", return_value=0), \
            patch("api.jobs_runs.find_by_idempotency_key", return_value=None), \
            patch("api.jobs_runs.create_run", return_value=run), \
            patch.object(q, "incr_inflight") as incr, \
            patch.object(q, "_get_queue") as get_q:
        get_q.return_value.enqueue = MagicMock()
        run_id = q._enqueue_or_dedup(
            user_id=USER_A,
            kind="g2_resume",
            payload={"job_id": 1},
            worker_func="api.worker.worker_run_g2",
        )
    assert run_id == "run-nocap"
    incr.assert_not_called()


def test_cap_enabled_helper():
    assert q._cap_enabled(0) is False
    assert q._cap_enabled(-5) is False
    assert q._cap_enabled(1) is True
    assert q._cap_enabled(50) is True


def test_default_cap_is_high_for_single_user():
    """Default MAX_INFLIGHT_PER_TENANT must be high enough that a single
    human user is never throttled (back-compat guarantee)."""
    from config.settings import Settings
    # Construct with only the required secrets so we read the field default.
    s = Settings(
        anthropic_api_key="x",
        openai_api_key="x",
        supabase_url="http://localhost",
        supabase_service_key="x",
    )
    assert s.max_inflight_per_tenant >= 50


# ─── worker DECR wrapper releases on success AND failure ─────────────────────
def test_worker_wrapper_releases_on_success():
    """The @_inflight_release wrapper must release a slot when the wrapped
    worker function returns normally."""
    import api.worker as w

    run = MagicMock()
    run.user_id = USER_A
    run.kind = "g2_resume"

    with patch.object(q, "_inflight_cap", return_value=50), \
            patch("api.jobs_runs.get_run", return_value=run), \
            patch("api.queue.release_inflight") as release:
        @w._inflight_release
        def fake_worker(jobs_run_id):
            return {"ok": True}

        out = fake_worker("run-1")
    assert out == {"ok": True}
    release.assert_called_once_with(USER_A, "g2_resume")


def test_worker_wrapper_releases_on_exception():
    """The wrapper must STILL release when the wrapped function raises (the
    worker re-raises failures for RQ retry — the slot must free regardless)."""
    import api.worker as w

    run = MagicMock()
    run.user_id = USER_A
    run.kind = "g5_score"

    with patch.object(q, "_inflight_cap", return_value=50), \
            patch("api.jobs_runs.get_run", return_value=run), \
            patch("api.queue.release_inflight") as release:
        @w._inflight_release
        def fake_worker(jobs_run_id):
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError):
            fake_worker("run-2")
    release.assert_called_once_with(USER_A, "g5_score")


def test_worker_wrapper_survives_missing_row():
    """If the run row can't be resolved (deleted between enqueue and pickup),
    the wrapper must not crash and must not attempt a release."""
    import api.worker as w

    with patch.object(q, "_inflight_cap", return_value=50), \
            patch("api.jobs_runs.get_run", return_value=None), \
            patch("api.queue.release_inflight") as release:
        @w._inflight_release
        def fake_worker(jobs_run_id):
            return {"error": "row_not_found"}

        out = fake_worker("ghost")
    assert out == {"error": "row_not_found"}
    release.assert_not_called()


def test_worker_wrapper_is_passthrough_when_disabled():
    """With the cap disabled, the wrapper must be a zero-overhead pass-through:
    no row read, no release attempt."""
    import api.worker as w

    with patch.object(q, "_inflight_cap", return_value=0), \
            patch("api.jobs_runs.get_run") as get_run, \
            patch("api.queue.release_inflight") as release:
        @w._inflight_release
        def fake_worker(jobs_run_id):
            return {"ok": True}

        out = fake_worker("run-x")
    assert out == {"ok": True}
    get_run.assert_not_called()   # no extra DB read
    release.assert_not_called()   # no Redis traffic


def test_worker_wrapper_never_crashes_on_release_error():
    """A release that somehow raises must not turn a successful job into a
    failure — the wrapper swallows it."""
    import api.worker as w

    run = MagicMock()
    run.user_id = USER_A
    run.kind = "g2_resume"

    with patch.object(q, "_inflight_cap", return_value=50), \
            patch("api.jobs_runs.get_run", return_value=run), \
            patch("api.queue.release_inflight", side_effect=RuntimeError("x")):
        @w._inflight_release
        def fake_worker(jobs_run_id):
            return {"ok": True}

        out = fake_worker("run-3")  # must not raise
    assert out == {"ok": True}


# ─── Parity with a real Redis double when fakeredis is installed ─────────────
try:
    import fakeredis  # type: ignore

    HAS_FAKEREDIS = True
except ImportError:  # pragma: no cover - depends on env
    HAS_FAKEREDIS = False

requires_fakeredis = pytest.mark.skipif(
    not HAS_FAKEREDIS,
    reason="fakeredis not installed in ~/crewai-env; `pip install fakeredis` to run",
)


@requires_fakeredis
def test_parity_with_real_redis_double():
    """The counter mechanics behave the same over a real fakeredis (bytes,
    real pipeline, real EXPIRE) as over the in-memory double."""
    server = fakeredis.FakeStrictRedis(decode_responses=False)
    with patch.object(q, "_get_redis", return_value=server), \
            patch.object(q, "_inflight_cap", return_value=2):
        assert q._over_inflight_cap(USER_A, "interactive")[0] is False
        assert q.incr_inflight(USER_A, "interactive") == 1
        assert q.incr_inflight(USER_A, "interactive") == 2
        assert q._over_inflight_cap(USER_A, "interactive")[0] is True  # at cap
        q.release_inflight(USER_A, "g2_resume")  # interactive
        assert q._over_inflight_cap(USER_A, "interactive")[0] is False
        # TTL safety net is set.
        assert server.ttl(q._inflight_key(USER_A, "interactive")) > 0


@requires_fakeredis
def test_parity_decrement_clamps_over_real_redis():
    server = fakeredis.FakeStrictRedis(decode_responses=False)
    with patch.object(q, "_get_redis", return_value=server), \
            patch.object(q, "_inflight_cap", return_value=3):
        q.decr_inflight(USER_A, "batch")  # from absent key
        raw = server.get(q._inflight_key(USER_A, "batch"))
        assert int(raw) == 0  # clamped to 0, never -1
