"""
api/rate_limits.py — Centralised slowapi Limiter + rate-limit tier table.

The Limiter is instantiated here (not in api/server.py) so the per-router
files (api/workspace.py, api/linkedin.py, api/network.py, …) can import it
without circular dependencies on `api.server`. api/server.py then attaches
the same instance onto `app.state.limiter` so SlowAPIMiddleware can find it.

Usage in routers:

    from api.rate_limits import limiter, RATE_LIMITS

    @router.post("/...")
    @limiter.limit(RATE_LIMITS["llm_generation"])
    async def handler(request: Request, ...):
        ...

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

from slowapi import Limiter
from slowapi.util import get_remote_address

RATE_LIMITS: dict[str, str] = {
    "llm_generation": "5/minute; 30/hour",
    "background_jobs": "2/minute; 10/hour",
    "data_import": "3/minute",
    "auth": "5/minute",
    "default": "60/minute",
}

# Single shared Limiter instance. api/server.py attaches this onto
# app.state.limiter so SlowAPIMiddleware can locate it; routers import this
# directly to decorate their routes.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[RATE_LIMITS["default"]],
)
