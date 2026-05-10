# Referral Graph (Network) — V1

> Audit ref: `docs/AUDIT_360_SYNTHESIS.md` §4 P1.1 ("Referral graph — half of
> the stated product vision; today 5% built").

This document is the authoritative reference for the `/network` surface:
schema, path-finder algorithm, the LinkedIn CSV ingest format, the
privacy stance, and how to plug the router into `api/server.py` once
the rest of Sprint 1 is on `main`.

---

## 1. Schema overview

The migration that ships this is
**`db/migrations/2026_05_10_004_referral_graph.sql`**. It is multi-tenant
from day 1 (every table carries `user_id uuid not null references
users(id)` and has RLS policies of the form `auth.uid() = user_id`).

### Tables

| Table | Purpose | Key columns |
|---|---|---|
| `people` | Anyone in the user's network. One row per (user_id, linkedin_url). The user's own node is `source='me'`. | `id`, `user_id`, `full_name`, `linkedin_url`, `email`, `headline`, `source`, `notes`, `profile_picture_url`, `location` |
| `employments` | Person ↔ company over time. `is_current` is a generated column: `ended_at IS NULL`. `company_name` is **denormalised** (see §4). | `id`, `person_id`, `company_id?`, `company_name`, `role`, `role_seniority`, `started_at`, `ended_at`, `is_current` |
| `edges` | Directed, typed, weighted connections between people. One typed edge per `(user_id, src, dst, kind)`. | `id`, `user_id`, `src_person`, `dst_person`, `kind`, `strength`, `evidence` |
| `target_company_employees` | Denormalised cache: target_company → currently-employed person. Refreshed by a worker job after CSV import or on demand. | `id`, `user_id`, `target_company_id`, `person_id`, `role`, `role_seniority`, `current_since`, `last_seen_at` |

### Edge kinds

```
me_first_degree         — the user → a directly-known person (LinkedIn 1°)
colleague               — same employer overlap
classmate               — same school
friend                  — non-professional but real
family
introduced_by           — third party introduced src to dst
same_company_overlap    — auto-inferred from employment intersection
referenced_in_outreach  — recruiter chain; "Bob said you should reach out"
```

### Indexes (for the path-finder)

- `(user_id)` on every table.
- `(user_id, full_name)` on `people` for substring search.
- `gin_trgm_ops` on `people.full_name` and `employments.company_name`
  for fuzzy matching.
- `(person_id) WHERE is_current` partial index on `employments` so the
  "current employer" lookup is O(log n).
- Unique `(user_id, src_person, dst_person, kind)` on `edges` so
  re-imports don't bloat the graph.

### RLS

Every table has `auth.uid() = user_id` policies for SELECT / INSERT /
UPDATE / DELETE. **Service-role bypasses RLS** — `api/server.py` runs
as service-role today, so the application layer enforces tenancy in
code (every endpoint filters `eq("user_id", user.id)`).

`employments` is special: it has no direct `user_id` column, so its RLS
policies join to `people.user_id`:

```sql
USING (EXISTS (SELECT 1 FROM people p
               WHERE p.id = employments.person_id
                 AND p.user_id = auth.uid()))
```

---

## 2. Path-finder algorithm

The path-finder lives in `agents/referral_graph.py::ReferralGraph.find_paths`.
It runs entirely in process — graphs are user-scoped (≤ ~5k people in V1)
and easily fit in memory.

### Steps

1. **Resolve the user's `me` node.** Every user has exactly one row
   tagged `source='me'`. If missing, return `[]` — the user hasn't
   imported anything yet.
2. **Pull candidate frontier.** Read `target_company_employees` for the
   given `target_company_id`. These are people the user knows who
   currently work at the target. (No frontier → no paths → empty.)
3. **Build a directed graph in memory.** For every `edges` row of the
   user, add one weighted directed edge with
   `weight = max(1 - strength, 1e-3)`. A higher strength → smaller
   weight, so Dijkstra's "shortest" path = strongest cumulative trust.
4. **Run Dijkstra per candidate.** For each frontier person, call
   `nx.shortest_path(G, source=me, target=person, weight='weight')`.
   Reject paths longer than `max_hops` (default 2) or with no edge.
5. **Score and rank.** Compute the **geometric mean** of edge strengths
   along the path. (Geometric mean — not arithmetic — penalises one
   weak link, which mirrors how real warm intros work.) Drop paths
   below `min_strength` (default 0.3). Sort by `(-strength, hops)` and
   return the top `limit`.

### Why Dijkstra, not BFS

BFS minimises hop count alone; that's wrong for our goal. A path
me → strong-tie → strong-tie → strong-tie can outperform
me → weak-tie → target. Weighting edges by `(1 - strength)` and using
Dijkstra picks the cumulative-trust optimum, capping hops at 2 keeps
results actionable.

### Why we cap at 2 hops in V1

Three or more hops crosses into "ask my friend's friend's friend",
which is empirically a weaker signal than a cold-outreach citing the
target's actual work. The audit (§4 P1.1) explicitly scopes V1 to
1- and 2-hop. Future work: opportunistic 3-hop only when no shorter
path exists.

---

## 3. Why `employments.company_name` is denormalised

`employments.company_id` references the canonical `companies` table.
But the vast majority of people in a real network work at companies
**we don't track**. Forcing `company_id NOT NULL` would mean either:

1. dropping every row where the company isn't in `companies` (loses
   network value), or
2. blocking imports until we resolve every name to an FK row (slow,
   error-prone).

So `company_name TEXT NOT NULL` is the source of truth, and `company_id`
is a soft FK the worker fills in when it can. Path-finding doesn't
care: `populate_target_company_employees()` does fuzzy name matching
against `target_companies.name` directly.

---

## 4. LinkedIn CSV ingest format

LinkedIn lets users export their connections as a CSV from
**Settings → Data privacy → Get a copy of your data → Connections**.
The format has been stable for years. The columns we read are exactly:

| Column | What we do with it |
|---|---|
| `First Name` | concatenated with Last Name → `people.full_name` |
| `Last Name` | concatenated with First Name → `people.full_name` |
| `URL` | `people.linkedin_url` (also the merge key) |
| `Email Address` | `people.email` (often blank — most LinkedIn exports redact email unless the user opted-in for the connection) |
| `Company` | `employments.company_name` (current row created with `ended_at` null → `is_current` true) |
| `Position` | `employments.role` and `people.headline` |
| `Connected On` | dropped onto `people.notes` as a free-form `connected_on=YYYY-MM-DD` so we can age out stale connections later |

Each row creates one `me_first_degree` edge from the user's `me` node
to the imported person, with `strength=0.7`. Re-imports are idempotent
because (a) `people` merges on `(user_id, linkedin_url)` and (b)
`edges` is unique on `(user_id, src, dst, kind)`.

After every CSV import, `populate_target_company_employees()` runs to
refresh the path-finder's frontier cache. The `/network/import/linkedin-csv`
endpoint does this inline; if you call the agent function directly,
remember to invoke the populator yourself.

---

## 5. Privacy & TOS — V1 stance

### What V1 does
- **Manual CSV import only.** The user actively exports their own data
  from LinkedIn and uploads it. We do not crawl, scrape, or call
  unofficial endpoints.
- All rows are scoped to the importing `user_id`. RLS prevents one user
  from reading another user's network even via Supabase REST.
- Service-role bypass is used by the FastAPI backend and the worker
  pool only. End-user JWT clients see only their own rows.

### What V1 does NOT do
- **No scraping.** Not LinkedIn, not the People API, not third-party
  scrapers (Apify, PhantomBuster, etc). The audit (§4 P1.1) flags this
  as a serious TOS / licensing risk; we explicitly avoid it.
- **No second-degree expansion from other users' networks.** Even if
  user A's friend Bob imports a CSV that includes user A's friend Sarah,
  user A's graph does NOT gain Sarah unless A imports their own CSV.
- **No outbound automation.** We draft messages locally; the user
  copies / mails / sends. We never POST to LinkedIn.

### What's planned (with explicit consent gates)
- Google contacts via OAuth read-only on `gmail.contacts.readonly`.
  Off by default; user must opt in per-import.
- Inference from outreach metadata (recruiter email chains the user
  has already received) — same idea as Superhuman's contacts panel.
- Auto-resolution of `company_id` on existing employments by matching
  `company_name` against the `companies` table.

If you ship anything beyond V1 here, update this section first.

---

## 6. How to wire the router into `api/server.py`

The router in `api/network.py` is **NOT** wired in yet. Out of an
abundance of caution (per the task brief: "DO NOT modify api/server.py"),
the include is left to a follow-up PR. When you wire it:

```python
# api/server.py — add near the bottom, after the other includes (if any)
from api.network import router as network_router

app.include_router(network_router)
```

Verify with:

```bash
curl -s "http://localhost:8000/openapi.json" \
  | jq '.paths | keys | map(select(startswith("/network")))'
# Should list /network/paths, /network/people, /network/import/linkedin-csv,
# /network/edges, /network/target-coverage
```

Endpoints all depend on `get_current_user` from `api/context.py`, so
they Just Work in single-user mode (Rizwan) and in multi-tenant mode
(JWT-authed) without any extra config.

---

## 7. Future work (in priority order)

1. **Real /network/draft-intro endpoint.** Today the dashboard's
   `IntroDraftModal` calls a stub. The endpoint should accept
   `{path: ReferralPath, target_role: dict, candidate_profile: dict,
   mode: 'intro_email' | 'target_outreach'}` and return the
   `IntroEmailAgent` output. Cost-cap and a queued variant for slow
   models go in `api/queue.py` alongside the G1/G2/G3 jobs.
2. **Google contacts OAuth ingestion.** Read-only scope; merge by
   email + linkedin_url; same de-dup logic as CSV.
3. **Auto-discovery of employments from outreach metadata.** When the
   user logs an outreach event, extract `from_email` / `linkedin_handle`
   and back-fill if matching person exists.
4. **Force-graph visualisation.** Worth it once the user has 500+ nodes.
   D3-force or react-force-graph. V1 explicitly skips this — a ranked
   list is more actionable for the first 100 nodes.
5. **Cohort-shared knowledge edges.** Once we have ≥ 50 users, allow
   opt-in surfacing of "people in your archetype landed at this target
   via this kind of intro" — privacy-preserving aggregate, never names.
6. **Proper pg_trgm RPC for `populate_target_company_employees`.** V1
   uses a Python Jaccard fallback because supabase-py's REST client
   doesn't expose pg_trgm operators. The pg_trgm GIN index is in place;
   wiring an RPC like `match_employments_to_targets(user_id)` is a
   1-day job.
