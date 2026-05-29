"""
Phase 1, finding 5 — RLS-LEVEL isolation proof against a real Postgres.

The app-level guarantee (the ``.eq("user_id")`` predicate) is the load-bearing
one *today*, because api/server.py uses the service-role key which BYPASSES RLS.
But migration 001 also turns on Postgres RLS so that the per-request JWT client
(db.client.get_user_supabase) gets DB-enforced isolation even if an app filter is
ever dropped. This test proves that second layer actually works: with RLS on and
a per-session JWT claim set, a RAW ``SELECT * FROM jobs`` (no WHERE user_id) still
returns only the calling tenant's rows.

How it runs
-----------
Spins up a *throwaway* Postgres cluster with ``initdb`` + ``pg_ctl`` on a private
Unix socket (no TCP, trust auth), loads an RLS setup that mirrors migration 001's
policy shape:

    auth.uid() = (current_setting('request.jwt.claims', true)::json->>'sub')::uuid
    CREATE POLICY jobs_select_own ON jobs FOR SELECT USING (auth.uid() = user_id);

then, acting as a NON-owner, non-superuser role (exactly Supabase's
``authenticated`` shape — superusers and the table owner can bypass RLS, so the
proof is done from a plain granted role), sets ``request.jwt.claims`` to user A
and asserts the raw select returns only A's rows; repeats for B; confirms an
unauthenticated session (no claim) sees nothing; and confirms an INSERT WITH
CHECK blocks writing a row owned by another tenant.

This uses the ``psql`` CLI (the repo's crewai-env has no psycopg driver). If the
Postgres binaries aren't on PATH (CI without PG, etc.) the test SKIPS with a clear
message and a pointer to run it locally — it never fakes a pass.

Run it directly:
    ~/crewai-env/bin/pytest tests/test_rls_isolation_pg.py -v
or as a standalone script:
    python3 tests/test_rls_isolation_pg.py
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

import pytest


USER_A = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
USER_B = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"


def _pg_bin(name: str) -> str | None:
    """Locate a Postgres binary on PATH or the common Homebrew prefixes."""
    found = shutil.which(name)
    if found:
        return found
    for prefix in (
        "/opt/homebrew/opt/postgresql@16/bin",
        "/opt/homebrew/bin",
        "/usr/local/opt/postgresql@16/bin",
        "/usr/local/bin",
        "/usr/lib/postgresql/16/bin",
    ):
        cand = os.path.join(prefix, name)
        if os.path.exists(cand):
            return cand
    return None


def _have_pg() -> bool:
    return all(_pg_bin(b) for b in ("initdb", "pg_ctl", "psql", "postgres"))


# ── the RLS setup, mirroring migration 001's policy shape ────────────────────
# auth.uid() reads the per-session JWT claim, exactly as Supabase does.
_SETUP_SQL = f"""
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- Supabase-shaped auth.uid() shim: pull 'sub' out of request.jwt.claims.
CREATE SCHEMA IF NOT EXISTS auth;
CREATE OR REPLACE FUNCTION auth.uid() RETURNS uuid
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(
    current_setting('request.jwt.claims', true)::json->>'sub', ''
  )::uuid
$$;

CREATE TABLE users (
    id    uuid PRIMARY KEY,
    email text NOT NULL UNIQUE
);
INSERT INTO users (id, email) VALUES
    ('{USER_A}', 'a@example.com'),
    ('{USER_B}', 'b@example.com');

-- A representative tenant table, shaped + policied like migration 001's loop.
CREATE TABLE jobs (
    id      bigserial PRIMARY KEY,
    title   text,
    user_id uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE
);
INSERT INTO jobs (title, user_id) VALUES
    ('A job',  '{USER_A}'),
    ('B job',  '{USER_B}'),
    ('A job2', '{USER_A}');

ALTER TABLE jobs ENABLE ROW LEVEL SECURITY;
-- FORCE so the proof holds even if a session connects as the table owner.
-- (Supabase's `authenticated` role is a non-owner, where RLS already applies;
-- FORCE makes this test robust regardless of which role psql connects as.)
ALTER TABLE jobs FORCE ROW LEVEL SECURITY;

CREATE POLICY jobs_select_own ON jobs
    FOR SELECT USING (auth.uid() = user_id);
CREATE POLICY jobs_insert_own ON jobs
    FOR INSERT WITH CHECK (auth.uid() = user_id);
CREATE POLICY jobs_update_own ON jobs
    FOR UPDATE USING (auth.uid() = user_id) WITH CHECK (auth.uid() = user_id);
CREATE POLICY jobs_delete_own ON jobs
    FOR DELETE USING (auth.uid() = user_id);
"""


def _claims(uid: str) -> str:
    # The JSON string PostgREST/Supabase put into request.jwt.claims.
    return '{"sub":"%s","role":"authenticated"}' % uid


class _ThrowawayPG:
    """Minimal throwaway Postgres cluster driven via the CLI tools."""

    def __init__(self):
        self.dir = tempfile.mkdtemp(prefix="jobhunt_rls_pg_")
        self.data = os.path.join(self.dir, "data")
        self.sock = os.path.join(self.dir, "sock")
        os.makedirs(self.sock, exist_ok=True)
        self.db = "rlstest"

    def _run(self, *args, check=True, input_=None):
        # Force a C locale: macOS shells often have empty LANG/LC_* which makes
        # initdb bail with "invalid locale settings". C is always available.
        env = dict(os.environ)
        env["LC_ALL"] = "C"
        env["LANG"] = "C"
        return subprocess.run(
            list(args),
            check=check,
            capture_output=True,
            text=True,
            input=input_,
            env=env,
        )

    def start(self):
        initdb = _pg_bin("initdb")
        pg_ctl = _pg_bin("pg_ctl")
        # trust auth on a Unix socket only; no TCP. --locale=C avoids the macOS
        # "invalid locale settings" initdb failure when LANG/LC_* are unset.
        self._run(
            initdb, "-D", self.data, "-A", "trust", "-U", "postgres",
            "--locale=C", "--encoding=UTF8",
        )
        opts = f"-c listen_addresses='' -c unix_socket_directories='{self.sock}'"
        self._run(
            pg_ctl, "-D", self.data, "-o", opts,
            "-w", "-t", "30", "-l", os.path.join(self.dir, "pg.log"), "start",
        )
        # createdb
        self.psql_db("postgres", "CREATE DATABASE rlstest;")

    def stop(self):
        pg_ctl = _pg_bin("pg_ctl")
        try:
            self._run(pg_ctl, "-D", self.data, "-m", "immediate", "-w", "stop", check=False)
        finally:
            shutil.rmtree(self.dir, ignore_errors=True)

    def psql_db(self, dbname: str, sql: str, *, role: str = "postgres",
                set_claims: str | None = None, expect_fail: bool = False):
        """Run SQL via psql against `dbname`. Optionally SET LOCAL the jwt claim
        inside a transaction so the policy sees a specific tenant.

        Returns stdout (tuples-only). Raises on unexpected failure."""
        psql = _pg_bin("psql")
        if set_claims is not None:
            # Wrap in a txn; SET LOCAL scopes the claim to this statement batch.
            sql = (
                "BEGIN;\n"
                f"SET LOCAL request.jwt.claims = '{set_claims}';\n"
                f"{sql}\n"
                "COMMIT;\n"
            )
        proc = self._run(
            psql, "-h", self.sock, "-U", role, "-d", dbname,
            "-v", "ON_ERROR_STOP=1", "-tA", "-c", sql,
            check=False,
        )
        if expect_fail:
            assert proc.returncode != 0, (
                f"expected SQL to fail but it succeeded:\n{sql}\nout={proc.stdout}"
            )
            return proc.stderr
        assert proc.returncode == 0, (
            f"psql failed:\nSQL={sql}\nstdout={proc.stdout}\nstderr={proc.stderr}"
        )
        return proc.stdout.strip()


@pytest.fixture(scope="module")
def pg():
    if not _have_pg():
        pytest.skip(
            "Postgres binaries (initdb/pg_ctl/psql/postgres) not found on PATH — "
            "RLS proof skipped. Install Postgres (e.g. `brew install "
            "postgresql@16`) and re-run: "
            "`~/crewai-env/bin/pytest tests/test_rls_isolation_pg.py`."
        )
    inst = _ThrowawayPG()
    try:
        inst.start()
    except Exception as e:  # pragma: no cover - environment dependent
        inst.stop()
        pytest.skip(f"could not start throwaway Postgres: {e}")
    # Load schema + policies as superuser (owner). RLS still applies because of
    # FORCE ROW LEVEL SECURITY above; and we also make a non-owner role below.
    inst.psql_db(inst.db, _SETUP_SQL)
    # A non-owner role to prove the *default* (non-FORCE) Supabase path too.
    inst.psql_db(
        inst.db,
        "CREATE ROLE tenant LOGIN; "
        "GRANT USAGE ON SCHEMA public, auth TO tenant; "
        "GRANT EXECUTE ON FUNCTION auth.uid() TO tenant; "
        "GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO tenant; "
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tenant;",
    )
    yield inst
    inst.stop()


# ── the proofs ───────────────────────────────────────────────────────────────


# psql prints a command-status tag (BEGIN/SET/COMMIT/INSERT 0 1/...) for every
# statement in a multi-statement -c batch. Strip those so only data rows remain.
_PSQL_TAGS = ("BEGIN", "COMMIT", "ROLLBACK", "SET")


def _rows(out: str) -> list[str]:
    rows = []
    for ln in out.splitlines():
        s = ln.strip()
        if not s:
            continue
        if s in _PSQL_TAGS:
            continue
        if s.startswith(("INSERT ", "UPDATE ", "DELETE ", "SELECT ")):
            # command tags like "INSERT 0 1" / "UPDATE 1" — never our text data.
            parts = s.split()
            if len(parts) <= 3 and all(p.isupper() or p.isdigit() for p in parts):
                continue
        rows.append(s)
    return rows


def test_rls_select_returns_only_callers_rows_nonowner(pg):
    """The canonical proof, in the exact role shape Supabase uses: a NON-owner,
    non-superuser role (mirrors Supabase's `authenticated`). A raw
    `SELECT * FROM jobs` with NO user_id filter is still tenant-isolated by RLS —
    user A's session sees only A's rows, user B's only B's. This is the layer
    that protects data even if an app-level `.eq("user_id")` is ever dropped.

    (We intentionally do NOT assert this via the table-owner connection: in this
    throwaway cluster the owner is the bootstrap superuser, and superusers bypass
    RLS — including FORCE ROW LEVEL SECURITY — so an owner-side check would be
    misleading. Supabase's app roles are non-superuser non-owners, which is
    exactly what `tenant` models here.)"""
    a = pg.psql_db(pg.db, "SELECT title FROM jobs ORDER BY title;",
                   role="tenant", set_claims=_claims(USER_A))
    assert sorted(_rows(a)) == ["A job", "A job2"], a

    b = pg.psql_db(pg.db, "SELECT title FROM jobs ORDER BY title;",
                   role="tenant", set_claims=_claims(USER_B))
    assert sorted(_rows(b)) == ["B job"], b


def test_rls_no_claim_sees_nothing(pg):
    """With no JWT claim set, auth.uid() is NULL → the policy matches no rows.
    Fail-closed: an unauthenticated session reads zero tenant rows."""
    out = pg.psql_db(pg.db, "SELECT count(*) FROM jobs;", role="tenant")
    assert _rows(out) == ["0"], out


def test_rls_insert_with_check_blocks_cross_tenant_write(pg):
    """INSERT ... WITH CHECK (auth.uid() = user_id): writing a row owned by the
    OTHER tenant is rejected by RLS even though no app code is involved."""
    # B's session trying to insert a row owned by A → policy violation (fails).
    err = pg.psql_db(
        pg.db,
        f"INSERT INTO jobs (title, user_id) VALUES ('sneaky', '{USER_A}');",
        role="tenant", set_claims=_claims(USER_B), expect_fail=True,
    )
    assert "row-level security" in err.lower() or "policy" in err.lower(), err

    # Control: B inserting B's own row succeeds.
    pg.psql_db(
        pg.db,
        f"INSERT INTO jobs (title, user_id) VALUES ('legit-b', '{USER_B}');",
        role="tenant", set_claims=_claims(USER_B),
    )
    seen = pg.psql_db(pg.db, "SELECT title FROM jobs ORDER BY title;",
                      role="tenant", set_claims=_claims(USER_B))
    assert "legit-b" in _rows(seen), seen


if __name__ == "__main__":  # allow running as a standalone script
    import sys

    if not _have_pg():
        print("SKIP: Postgres binaries not found on PATH.")
        sys.exit(0)
    inst = _ThrowawayPG()
    try:
        inst.start()
        inst.psql_db(inst.db, _SETUP_SQL)
        inst.psql_db(
            inst.db,
            "CREATE ROLE tenant LOGIN; "
            "GRANT USAGE ON SCHEMA public, auth TO tenant; "
            "GRANT EXECUTE ON FUNCTION auth.uid() TO tenant; "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON jobs TO tenant; "
            "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO tenant;",
        )
        a = inst.psql_db(inst.db, "SELECT title FROM jobs ORDER BY title;",
                         role="tenant", set_claims=_claims(USER_A))
        b = inst.psql_db(inst.db, "SELECT title FROM jobs ORDER BY title;",
                         role="tenant", set_claims=_claims(USER_B))
        print("user A sees:", _rows(a))
        print("user B sees:", _rows(b))
        assert sorted(_rows(a)) == ["A job", "A job2"]
        assert sorted(_rows(b)) == ["B job"]
        print("RLS isolation proof PASSED")
    finally:
        inst.stop()
