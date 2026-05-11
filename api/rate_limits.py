"""
api/rate_limits.py — Centralised rate-limit tiers for the FastAPI surface.

The Limiter itself lives in api/server.py (it must be on app.state so
SlowAPIMiddleware can find it). This module is just the lookup table —
import RATE_LIMITS where you need to apply a specific tier and the limiter
instance from `api.server` to call `@limiter.limit(RATE_LIMITS["..."])`.

Tier rationale
==============

llm_generation     5/min;  30/hour    G2 resume builds, G3 prep, G4 LinkedIn
                                      drafts. Each call costs $0.50–$5 and
                                      runs for 30–60s; 5/min is roughly the
                                      ceiling a single human ever clicks.

background_jobs    2/min;  10/hour    Scout pipeline runs. These spawn DB +
                                      LLM work that lingers; bursting them
                                      hurts more than helps.

data_import        3/min               LinkedIn CSV upload. The bottleneck is
                                      the populator that re-indexes the
                                      referral graph; 3/min is plenty.

auth               5/min               Reserved for future /auth/* routes
                                      (signup, login, token refresh).
                                      Currently unused because we sit
                                      behind RIZWAN_SINGLE_USER_MODE=1.

default            60/min              Read-heavy routes (list jobs, get
                                      persona, fetch run status). Applied
                                      globally by SlowAPIMiddleware so we
                                      don't have to decorate every route
                                      inline.

Multi-window strings (e.g. "5/minute; 30/hour") are slowapi's native syntax
for "trip on the first window that fills first" — short bursts are throttled
by the per-minute limit, sustained abuse by the per-hour limit.

TODO(multi-tenant): when we leave single-user mode the key function should
move from get_remote_address to a user_id-aware lookup so several users
behind the same NAT don't trip each other's limits. See
docs/AUDIT_REVIEW_EXTERNAL_2026_05_12.md §3.6 (P1-3).
"""
from __future__ import annotations

RATE_LIMITS: dict[str, str] = {
    "llm_generation": "5/minute; 30/hour",
    "background_jobs": "2/minute; 10/hour",
    "data_import": "3/minute",
    "auth": "5/minute",
    "default": "60/minute",
}
