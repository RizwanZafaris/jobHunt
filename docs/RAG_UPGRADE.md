# jobHunt — RAG Pipeline: current state + upgrade plan

**Date:** 2026-05-31 · **Lens:** how to lift the AI agents' output quality
(personas, résumés, interview prep, intros) by improving *retrieval*, not just
prompts/models.

> **Read this first — the binding constraint.** RAG quality is bounded by the
> knowledge base it retrieves from, and that base is currently **starved**: the
> persona/company knowledge is built by deep-research that pulls web data via
> **Apify**, and Apify is returning **0 sources** (token missing/expired/
> out-of-credit — confirm with `/debug/apify-check`). Every retrieval upgrade
> below is real and worth doing, but **none of them move the needle until the
> knowledge base is repopulated.** Sequence: **fix Apify → rebuild personas
> (`POST /admin/personas/rebuild-all`) → then ship RAG upgrades against a
> populated corpus.** Building retrieval polish on an empty index is unfalsifiable.

---

## 1. What RAG already exists (this is NOT greenfield)

jobHunt already has a working **vector-retrieval layer** — the upgrades are
refinements, not a from-scratch build.

**Embedding + store** (`db/client.py`)
- `embed(text)` → OpenAI `text-embedding-3-small` (1536-d). One model, one place.
- `upsert_company_knowledge()` / `upsert_rizwan_profile()` / story-bank writers
  store content + embedding into pgvector columns.

**Three retrieval corpora**, each a Postgres RPC over pgvector cosine:
| Corpus | RPC | Helper | Consumed by |
|---|---|---|---|
| Company intel | `search_company_knowledge` (mig 009) | `search_company_knowledge()` | g2_nodes (insider expert), company_agent, g6_io, g7_nodes, interview_agent |
| Your profile | `search_rizwan_profile` | `search_rizwan_profile()` | rizwan_agent |
| STAR stories | `search_story_bank_v2` (mig 018) | `search_story_bank()` / story_bank_agent | g3/g7 interview prep, interview_agent, rizwan_agent |

**Indexing**: migration 041 added **HNSW** indexes on the knowledge/story/profile
vector columns (good — fast ANN at scale).

**Outcome-credit scaffolding already exists** (mig 008): `search_company_knowledge`
returns each chunk's `id`; `g2_nodes.insider_expert_node` embeds it back into the
transcript as `cite:knowledge_id=<uuid>`; `outcome_to_persona.credit_outcome`
can attribute interview outcomes to the exact chunks a résumé cited. **The wiring
for outcome-conditioned RAG is laid but not used in ranking yet.**

So the system is a **solid v1 RAG**: single-vector, top-k cosine, HNSW-indexed,
tenant-scoped, with a latent feedback loop. The gaps below are what separate v1
from a precision retrieval system.

---

## 2. The 6 gaps (each = a concrete upgrade)

### G1 — No reranking (retrieve = final)  ·  impact: HIGH · effort: M
Every caller takes the raw top-k cosine hits (`match_count=3–5`) straight into the
prompt. Cosine top-5 is noisy: the 5th hit is often only weakly relevant, and a
genuinely on-point chunk ranked #8 never makes it in.
**Fix:** retrieve top-20, then rerank to top-5 with either a cross-encoder
(Cohere/Voyage rerank API) or a cheap LLM rerank (Haiku: "score each passage 0-10
for relevance to <query>"). Implement once in `db/client.py` as a
`search_*_reranked()` wrapper; opt-in per caller. Biggest single precision win.

### G2 — No freshness/decay weighting  ·  impact: HIGH · effort: S
`search_company_knowledge` returns `scraped_at` but **nothing uses it** — a
2-year-old funding stat ranks equal to last week's. (Note: there is **no**
`confidence_decays_at` column today — `grep` finds it nowhere in the repo, so a
decay-timestamp would be net-new schema, not a wiring-up of something existing.)
**Fix:** blend recency into the score: `final = α·similarity + β·recency_decay`
(exponential on `scraped_at`, which IS stored). Optionally add a
`confidence_decays_at` column to hard-drop expired chunks. Pure SQL change in the
RPC + a weight in the helper. Cheap, high-value for persona accuracy (company
intel goes stale fast).

### G3 — Outcome-conditioned ranking is built but unused  ·  impact: HIGH (compounding) · effort: M
The `cite:knowledge_id` → `credit_outcome` loop records which chunks led to
interviews, but retrieval never boosts them. This is jobHunt's *unique* RAG edge:
a corpus that learns what actually wins.
**Fix:** add an `outcome_score` column (credited interviews − rejections per
chunk), blend into ranking: `final = α·sim + β·recency + γ·outcome`. Compounds
over time — the more you apply, the smarter retrieval gets.

### G4 — `jobs.jd_embedding` is dead weight  ·  impact: MED · effort: S
Every job stores a 1536-d JD embedding (6 KB/row) but **nothing ever queries it**
(confirmed: zero `<=>` reads; migration 041 explicitly skipped indexing it).
Two honest options:
- **Use it**: "find me jobs similar to ones I've liked/applied to" + dedup
  near-identical reposts across boards (real value on /today). Needs an HNSW
  index + a `similar_jobs` RPC.
- **Drop it**: if no similarity feature is planned, remove the column — it's pure
  storage + write-time embedding cost. **Decide, don't leave it half-built.**

### G5 — Fixed-size / section-based chunking, no semantic chunking  ·  impact: MED · effort: M
Company knowledge is chunked by persona *section*; long sources get truncated
(`max_chars`). No overlap, no semantic boundaries → retrieval can miss facts that
straddle a cut.
**Fix:** semantic/recursive chunking with small overlap on ingest (persona
deep-research + company_agent). Improves recall; only matters once Apify feeds
real volume (ties to the dependency above).

### G6 — Pure vector, no hybrid (keyword) retrieval  ·  impact: MED · effort: M
ATS keyword matching is **exact-term** by nature ("Workday", "PCI-DSS",
"issuing"), but vector search can miss exact tokens a recruiter greps for.
**Fix:** add Postgres full-text (`tsvector`) search and fuse with vector via
**Reciprocal Rank Fusion**. Best recall for the ATS-keyword use case specifically.
New SQL RPC + a GIN index.

---

## 3. Sequenced plan (after Apify is restored)

**Phase R0 — unblock the corpus (prerequisite, not RAG code)**
Fix `APIFY_TOKEN` → `POST /admin/personas/rebuild-all` → confirm personas reach
`medium`/`high`. Without this, R1–R3 are untestable.

**Phase R1 — precision (highest impact, lowest risk):** G2 (freshness/decay) +
G1 (reranking). Both centralize in `db/client.py` + the RPCs; opt-in per caller
so single-user behaviour is unchanged until flipped. ~2 PRs.

**Phase R2 — the moat:** G3 (outcome-conditioned ranking). Turns the existing
credit loop into a ranking signal. ~1 PR + a backfill of `outcome_score`.

**Phase R3 — recall:** G6 (hybrid RRF) + G5 (semantic chunking). Bigger SQL +
ingest changes; do once volume justifies it.

**Phase R4 — decide jd_embedding (G4):** ship `similar_jobs` OR drop the column.
Independent of the others.

### Guardrails for every RAG PR
- **Flag-gated / opt-in** so single-user prod is byte-for-byte unchanged until enabled.
- **Eval before/after**: build a tiny fixed query set (10–20 persona/résumé
  queries) and measure retrieval precision@5 pre/post — otherwise "better RAG" is
  a vibe, not a result. (There is no eval harness today; R1 should add one.)
- **Cost-aware**: reranking adds an LLM/API call per retrieval — cache by
  (query_hash, corpus_version); the prompt-cache + cost-rollup infra already exists.
- **Tenant-scoped**: all new RPCs keep the `user_id` predicate (the static
  guardrail test enforces this).

---

## 4. One-line summary
jobHunt has a real single-vector RAG with HNSW + a latent outcome-credit loop.
The highest-leverage upgrades are **reranking + freshness weighting** (precision)
and **activating outcome-conditioned ranking** (a compounding moat) — but all of
it is gated on **repopulating the Apify-starved knowledge base first**. Build R1
against a real corpus, measure precision@5, and only then layer hybrid + semantic
chunking.
