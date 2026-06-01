"""
api/traces.py — LangGraph trace observability REST API (Stream C, 2026-05-13).

Three read-only endpoints feeding /insights?tab=traces — answers "what is
my LangGraph agent doing right now?":

  GET /traces/recent        — last 50 graph runs from v_graph_runs
  GET /traces/{graph}/{run_key}  — per-node drill-down for one run
  GET /traces/errors        — last 20 agent_call_log rows where error
                              IS NOT NULL

User feedback (verbatim 2026-05-13):
  "Analytics should show how my Agent in lang graph are working,
   this can be used to debug. I don't know what it is built for."

The /insights?tab=analytics page I built earlier shows application funnel
conversion data — useful at scale but not what the user asked for.
This is the actual ask.

No wallet guard required (pure read-only over agent_call_log + the
v_graph_runs view shipped in migration 029). Cache headers: private,
max-age=15 — analytics shifts slowly but graph traces should feel live.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Path, Query, Response
from pydantic import BaseModel, Field

from api.context import get_current_user
from api.users import User
from db.client import aexecute, get_supabase

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/traces", tags=["traces"])


# ── Response models ─────────────────────────────────────────────────────


class GraphRunSummary(BaseModel):
    graph: str
    run_key: str
    started_at: Optional[str] = None
    last_event_at: Optional[str] = None
    total_nodes: int = 0
    error_nodes: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: int = 0
    nodes_executed: list[str] = Field(default_factory=list)
    status: str = "unknown"  # 'converged' | 'in_progress' | 'failed'


class RecentRunsResponse(BaseModel):
    runs: list[GraphRunSummary] = Field(default_factory=list)
    message: str = ""


class NodeEvent(BaseModel):
    node_name: Optional[str] = None
    agent_name: Optional[str] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    cost_usd: float = 0.0
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: Optional[str] = None
    called_at: Optional[str] = None


class TraceDetailResponse(BaseModel):
    graph: str
    run_key: str
    total_nodes: int = 0
    total_cost_usd: float = 0.0
    total_latency_ms: int = 0
    status: str = "unknown"
    nodes: list[NodeEvent] = Field(default_factory=list)


class ErrorEvent(BaseModel):
    graph: Optional[str] = None
    node_name: Optional[str] = None
    agent_name: Optional[str] = None
    model: Optional[str] = None
    error: str
    called_at: Optional[str] = None
    run_key: Optional[str] = None


class ErrorsResponse(BaseModel):
    errors: list[ErrorEvent] = Field(default_factory=list)
    message: str = ""


# ── Helpers ─────────────────────────────────────────────────────────────


def _set_cache(response: Response) -> None:
    response.headers["Cache-Control"] = "private, max-age=15"


def _resolve_run_key(row: dict) -> str:
    """Mirror the v_graph_runs COALESCE so /traces/{graph}/{run_key} is
    addressable from a row's identifiers in the same priority order."""
    if row.get("application_id"):
        return str(row["application_id"])
    if row.get("resume_build_id"):
        return str(row["resume_build_id"])
    if row.get("job_id") is not None:
        return f"job:{row['job_id']}"
    return "orphan"


# ── Endpoints ───────────────────────────────────────────────────────────


@router.get("/recent", response_model=RecentRunsResponse)
async def recent_runs(
    response: Response,
    user: User = Depends(get_current_user),
    limit: int = Query(default=50, ge=1, le=200),
) -> RecentRunsResponse:
    """Last N graph runs for the current user, newest first."""
    _set_cache(response)
    db = get_supabase()
    try:
        rows = (await aexecute(
            db.table("v_graph_runs")
            .select("*")
            .eq("user_id", str(user.id))
            .order("last_event_at", desc=True)
            .limit(limit)
        )).data or []
    except Exception as exc:
        logger.exception("v_graph_runs query failed for user %s", user.id)
        return RecentRunsResponse(
            runs=[],
            message=f"v_graph_runs view not available: {exc!r}",
        )

    runs = [
        GraphRunSummary(
            graph=r.get("graph", "unknown"),
            run_key=r.get("run_key", "orphan"),
            started_at=r.get("started_at"),
            last_event_at=r.get("last_event_at"),
            total_nodes=int(r.get("total_nodes") or 0),
            error_nodes=int(r.get("error_nodes") or 0),
            total_cost_usd=float(r.get("total_cost_usd") or 0.0),
            total_latency_ms=int(r.get("total_latency_ms") or 0),
            nodes_executed=list(r.get("nodes_executed") or []),
            status=r.get("status") or "unknown",
        )
        for r in rows
    ]
    return RecentRunsResponse(
        runs=runs,
        message="" if runs else "No graph runs in the last 7 days.",
    )


@router.get("/errors", response_model=ErrorsResponse)
async def recent_errors(
    response: Response,
    user: User = Depends(get_current_user),
    limit: int = Query(default=20, ge=1, le=100),
) -> ErrorsResponse:
    """Last N error rows from agent_call_log."""
    _set_cache(response)
    db = get_supabase()
    try:
        rows = (await aexecute(
            db.table("agent_call_log")
            .select(
                "graph, node_name, agent_name, model, error, called_at, "
                "application_id, resume_build_id, job_id"
            )
            .eq("user_id", str(user.id))
            .not_.is_("error", "null")
            .order("called_at", desc=True)
            .limit(limit)
        )).data or []
    except Exception as exc:
        logger.exception("agent_call_log errors query failed: %r", exc)
        return ErrorsResponse(
            errors=[],
            message=f"agent_call_log not available: {exc!r}",
        )

    errors = [
        ErrorEvent(
            graph=r.get("graph"),
            node_name=r.get("node_name"),
            agent_name=r.get("agent_name"),
            model=r.get("model"),
            error=str(r.get("error") or "unknown")[:500],
            called_at=r.get("called_at"),
            run_key=_resolve_run_key(r),
        )
        for r in rows
    ]
    return ErrorsResponse(
        errors=errors,
        message="" if errors else "No errors in the last 7 days. ✓",
    )


@router.get("/{graph}/{run_key}", response_model=TraceDetailResponse)
async def trace_detail(
    response: Response,
    graph: str = Path(..., min_length=1, max_length=64),
    run_key: str = Path(..., min_length=1, max_length=128),
    user: User = Depends(get_current_user),
) -> TraceDetailResponse:
    """Per-node drill-down for a single (graph, run_key) pair.

    run_key matches v_graph_runs.run_key: application_id (UUID) for G7/G8,
    resume_build_id (UUID) for G2, or "job:<id>" for graphs scoped to jobs.
    """
    _set_cache(response)
    db = get_supabase()

    # Determine which column to filter on from the run_key shape.
    q = (
        db.table("agent_call_log")
        .select(
            "node_name, agent_name, provider, model, cost_usd, latency_ms, "
            "input_tokens, output_tokens, error, called_at"
        )
        .eq("user_id", str(user.id))
        .eq("graph", graph)
    )

    if run_key.startswith("job:"):
        try:
            job_id = int(run_key.split(":", 1)[1])
        except ValueError:
            return TraceDetailResponse(graph=graph, run_key=run_key)
        q = q.eq("job_id", job_id).is_("application_id", "null").is_("resume_build_id", "null")
    else:
        # Could be application_id (UUID) or resume_build_id (UUID).
        # PostgREST .or_ takes a string filter; use it to try both.
        q = q.or_(f"application_id.eq.{run_key},resume_build_id.eq.{run_key}")

    try:
        rows = (await aexecute(q.order("called_at", desc=False))).data or []
    except Exception as exc:
        logger.warning("trace_detail query failed: %r", exc)
        return TraceDetailResponse(graph=graph, run_key=run_key)

    nodes = [
        NodeEvent(
            node_name=r.get("node_name"),
            agent_name=r.get("agent_name"),
            provider=r.get("provider"),
            model=r.get("model"),
            cost_usd=float(r.get("cost_usd") or 0.0),
            latency_ms=int(r.get("latency_ms") or 0),
            input_tokens=int(r.get("input_tokens") or 0),
            output_tokens=int(r.get("output_tokens") or 0),
            error=r.get("error"),
            called_at=r.get("called_at"),
        )
        for r in rows
    ]
    has_error = any(n.error for n in nodes)
    status = "failed" if has_error else ("in_progress" if not nodes else "converged")
    return TraceDetailResponse(
        graph=graph,
        run_key=run_key,
        total_nodes=len(nodes),
        total_cost_usd=round(sum(n.cost_usd for n in nodes), 4),
        total_latency_ms=sum(n.latency_ms for n in nodes),
        status=status,
        nodes=nodes,
    )
