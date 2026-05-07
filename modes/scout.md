# Mode: scout
# Find new job openings across Gulf and UK markets

## Purpose
Discover qualifying job openings via ATS APIs, Serper search, and portal scanning.
Filter to roles with match_score >= 40. Validate all URLs for expiry before storing.

## Target Roles (search terms)
- "Head of Product"
- "Chief Product Officer" / "CPO"
- "VP Product" / "VP of Product"
- "Senior Product Manager"
- "Technical Programme Manager" / "Technical Program Manager"
- "Head of Programme Management" / "Head of PMO"
- "Director of Product"
- "Product Lead" (senior context only)
- "Head of Payments Product"
- "Head of Digital Banking"
- "Payments Product Manager"
- "Digital Banking Product Lead"

## Target Markets
- UAE: LinkedIn, Bayt, NaukriGulf, GulfTalent, Dubizzle Jobs, Indeed Gulf
- KSA: LinkedIn, Bayt, GulfTalent, STC Pay/Tamara/Tabby career pages
- Qatar: LinkedIn, Bayt
- Bahrain: LinkedIn, Bayt
- UK: LinkedIn, TechCrunch Jobs, Otta, Cord
- Remote/Global: Remote.com, We Work Remotely, LinkedIn Remote

## ATS Direct Scan

### Priority Schedule
Companies in portals.yml are assigned priority 1, 2, or 3:
- **P1 (daily)**: Tier-1 targets — Stripe, Adyen, Checkout.com, Tabby, Wio Bank, Airwallex, Wise, Revolut, Network International, Magnati, Mastercard London, Visa London
- **P2 (every 2 days)**: Strong targets — Tamara, Postpay, STC Pay, Nium, Rapyd, Currencycloud, Monzo, Starling, N26, PayPal, GoCardless, Marqeta, Plaid
- **P3 (every 5 days)**: Long-tail — traditional banks, crypto-adjacent, secondary markets

Rotation formula: `day_of_year % interval == 0` — P2 scans alternate days, P3 scans every 5th day.

### ATS Type Coverage
| ATS | Method | Notes |
|-----|--------|-------|
| `greenhouse` | REST GET `/boards/{slug}/jobs` | Returns JSON list |
| `lever` | REST GET `/v0/postings/{slug}?mode=json` | Returns JSON list |
| `ashby` | GraphQL `ApiJobBoardWithTeams` | Falls back to HTML `window.__appData` scrape |
| `workday` | HTML scrape + URL pattern match | Fallback only |
| `smartrecruiters` | REST GET `/companies/{slug}/postings` | |

All ATS slugs are in `portals.yml` under the `ats_slug` field.

## Serper Query Rotation

The agent runs **12 Serper queries per day**, rotating through the full 28-query bank
using a day-of-year offset: `start = day_of_year % total_queries`. This ensures every
query gets used over a rolling ~2 day cycle rather than always hitting the same first 8.

Key query categories:
- Role + market (UAE, KSA, London, Remote) — 2026 date filter
- `site:greenhouse.io`, `site:lever.co`, `site:ashby.io` — direct ATS index queries
- Gulf job portals: Bayt, NaukriGulf, GulfTalent, Indeed Gulf
- Specific companies: Tabby, Wio Bank, Checkout.com, Airwallex, Wise, Revolut
- KSA-specific: Saudi Arabia fintech senior PM/product roles

## Expiry Validation

Every newly discovered job URL is validated **before** being stored:

1. **HTTP status check**: 404 or 410 → mark expired immediately
2. **Content signal scan**: Page body scanned for phrases like:
   - "this job is no longer accepting applications"
   - "position has been filled"
   - "this listing has expired"
   - "job has been closed"
3. **Redirect detection**: If URL redirects to a generic `/jobs` or `/careers` page, treat as expired

HTTP 403 (bot-blocked) → assume valid and store. 429 → assume valid, log warning.

### Nightly DB Expiry Cleanup
At the end of each run, the agent checks the **50 most recent** jobs with status
`new` or `evaluated` in the DB and re-validates their URLs. Any that now return
expiry signals are updated to `status = 'expired'` with the reason logged in
`fit_details.expiry_reason`.

## Deduplication Rules

Two-layer deduplication is applied before any DB write:

1. **URL dedup**: Exact URL match → skip
2. **Content hash dedup**: MD5 of `company_normalised|title_normalised|location_normalised`
   — catches Workday and other ATS reposts where the same job gets a new URL

DB freshness filter: Jobs with status other than `new` are also skipped to avoid
re-processing already-evaluated or applied roles.

## Red Flag Penalties (post-scoring, deterministic)

Applied by regex AFTER GPT scoring to override any LLM inconsistencies:

| Pattern | Penalty | Reason |
|---------|---------|--------|
| `uae.{0,10}national` | -30 | UAE Nationals preferred |
| `gcc.{0,10}national` | -20 | GCC Nationals preferred |
| `emirati.{0,10}preferred` | -30 | Emirati preferred |
| `graduate programme` / `intern` | -25 | Entry-level despite senior title |
| `arabic.{0,10}(required\|essential\|must)` | -20 | Arabic required |
| Mastercard/Visa/Amex in Dubai + non-senior title | -25 | Network companies Dubai = junior/nationals |

Geography rules:
- **Mastercard / Visa Dubai posts**: assume UAE Nationals preferred → -30 applied automatically unless JD explicitly says "open to all nationalities"
- **Mastercard / Visa London / Amsterdam / Singapore**: no nationality penalty — score normally
- **Adyen**: score > 60 only if role is Dubai or explicitly remote-friendly
- **Stripe**: note high interview bar in `red_flags` but do not penalise score

## Output Format
```json
{
  "job_id": "unique-hash",
  "title": "Head of Product",
  "company": "Tabby",
  "location": "Dubai, UAE",
  "url": "https://...",
  "source": "ats_direct|serper|portal",
  "ats_type": "ashby|greenhouse|lever|workday|smartrecruiters",
  "description": "...",
  "match_score": 82,
  "url_valid": true,
  "expiry_reason": null,
  "discovered_at": "2026-01-15T09:00:00",
  "red_flags_applied": ["UAE Nationals preferred (-30)"]
}
```

## Run Order
```
1. ATS portal scan (portals.yml, priority-scheduled)
2. Serper search (12 queries, day-of-year rotation)
3. Deduplicate (URL + content hash)
4. Filter already-known (DB freshness check)
5. Validate URLs (HTTP + content + redirect)
6. Score with GPT-4.1
7. Apply red flag penalties (deterministic regex)
8. Store to DB
9. Nightly expiry cleanup (50 most recent)
```
