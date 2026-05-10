# db/migrations

> **DO NOT apply `2026_05_10_001_multi_tenancy.sql` to production until tested
> on a Supabase branch. Run `db/migrations/APPLY.sh` against a branched DB
> first.** Multi-tenancy adds NOT NULL columns and enables RLS; a botched
> apply on prod will lock the API out instantly.

This folder holds versioned, append-only SQL migrations. The single
source-of-truth schema (`db/schema.sql`) is *not* edited; instead, every
schema change is added here as a new timestamped file.

---

## Apply order

Migrations apply in lexical order. Today:

| # | File | Owner | What |
|---|------|-------|------|
| 001 | `2026_05_10_001_multi_tenancy.sql` | this agent | `users`, `orgs`, `user_id`/`org_id` on per-tenant tables, RLS + policies |
| 002 | `2026_05_10_002_status_enum.sql` | this agent | Canonical `application_status` enum + Spanish→English backfill |
| 003 | `2026_05_10_003_queue_worker.sql` | **other agent** | Queue worker tables/functions (referenced here so the apply script numbers right; not authored in this PR) |

After the seed:

```bash
psql "$DATABASE_URL" -f db/seeds/user_001.sql
```

The seed must run **after** 001 (the table doesn't exist before that) and
**before** any tenant-aware insert path is exercised.

---

## Pre-flight

1. Open a Supabase branch:
   ```bash
   # via dashboard or:
   #   supabase branches create test-multitenancy
   ```
2. Set `DATABASE_URL` to the branch's connection string.
3. Run `bash db/migrations/APPLY.sh` (it will detect non-localhost and
   require a `yes` confirmation).
4. Smoke-test the API against the branch DB.
5. Only then run the same script against the production `DATABASE_URL`.

---

## Rollback (best-effort)

Migrations under `db/migrations/` are intended to be **forward-only**.
The blocks below are the *last-resort* rollback recipes. They are
destructive — running them on a populated production DB will drop columns
and break the API. Do not paste blindly.

### Rollback 002 — `application_status` enum

```sql
BEGIN;
ALTER TABLE applications DROP CONSTRAINT IF EXISTS applications_status_valid;
ALTER TABLE applications ALTER COLUMN status DROP DEFAULT;
-- enum → text (preserves the labels; no data loss)
ALTER TABLE applications
    ALTER COLUMN status TYPE TEXT
    USING status::text;
ALTER TABLE applications ALTER COLUMN status SET DEFAULT 'new';
DROP TYPE IF EXISTS application_status;
NOTIFY pgrst, 'reload schema';
COMMIT;
```

Notes:
- This *keeps* the canonicalised values ('researched', 'offered', etc.)
  rather than reverting to 'Evaluada'. There is no automatic way to
  recover the original Spanish labels — they were one-way mapped.
- If you need the original `'Evaluada'` default back, add it manually
  *after* the ALTER COLUMN above.

### Rollback 001 — multi-tenancy

```sql
-- WARNING: dropping user_id is destructive and breaks every API endpoint.
-- This only makes sense on a Supabase branch you intend to delete anyway.
BEGIN;
DO $rb$
DECLARE
    target_tables CONSTANT TEXT[] := ARRAY[
        'jobs','applications','company_personas','personas',
        'company_knowledge','target_companies','costs','outreach'
    ];
    t TEXT;
BEGIN
    FOREACH t IN ARRAY target_tables LOOP
        IF EXISTS (
            SELECT 1 FROM information_schema.tables
            WHERE table_schema = 'public' AND table_name = t
        ) THEN
            EXECUTE format('ALTER TABLE %I DISABLE ROW LEVEL SECURITY', t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', format('%s_select_own', t), t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', format('%s_insert_own', t), t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', format('%s_update_own', t), t);
            EXECUTE format('DROP POLICY IF EXISTS %I ON %I', format('%s_delete_own', t), t);
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I', t, format('%s_user_id_fkey', t));
            EXECUTE format('ALTER TABLE %I DROP CONSTRAINT IF EXISTS %I', t, format('%s_org_id_fkey', t));
            EXECUTE format('DROP INDEX IF EXISTS %I', format('idx_%s_user_id', t));
            EXECUTE format('DROP INDEX IF EXISTS %I', format('idx_%s_org_id', t));
            EXECUTE format('ALTER TABLE %I DROP COLUMN IF EXISTS user_id', t);
            EXECUTE format('ALTER TABLE %I DROP COLUMN IF EXISTS org_id', t);
        END IF;
    END LOOP;
END
$rb$;

DROP TABLE IF EXISTS orgs;
DROP TABLE IF EXISTS users;
NOTIFY pgrst, 'reload schema';
COMMIT;
```

### Rollback 003

Owned by the queue-worker agent — see that PR for its rollback recipe.

---

## Adding a new migration

1. Pick the next ordinal:
   ```
   YYYY_MM_DD_NNN_short_slug.sql
   ```
   `NNN` is the per-day counter (`001`, `002`, ...).
2. Wrap the body in `BEGIN; ... COMMIT;`.
3. Use `IF NOT EXISTS` / `DROP ... IF EXISTS` everywhere — re-running the
   script must be a no-op.
4. End with `NOTIFY pgrst, 'reload schema';` so Supabase's PostgREST
   reflects new tables/columns immediately.
5. Update this README's table.
6. Test on a Supabase branch.
