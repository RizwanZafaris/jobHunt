"""
Step 8: Run CompanyAgent.build_or_refresh() on every target company.
Each target gets ~13 knowledge sections including recruitment intelligence.
Resumes mid-run if interrupted (idempotent — uses on_conflict).

Costs ~$5-15 in Claude tokens depending on cache hits.

Usage:
    python3 profile_build/08_research_targets.py
    python3 profile_build/08_research_targets.py --priority high
    python3 profile_build/08_research_targets.py --names "Stripe,Adyen,Wise"
    python3 profile_build/08_research_targets.py --concurrency 3
"""
from __future__ import annotations
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from db.client import get_supabase
from agents.company_agent import CompanyAgent


async def research_one(name: str, force: bool = False) -> dict:
    """Run CompanyAgent on one company; returns status dict."""
    try:
        agent = CompanyAgent(name)
        result = await agent.build_or_refresh(force=force)
        return {"name": name, "ok": True, "status": result.get("status"), "sections": result.get("sections", [])}
    except Exception as e:
        return {"name": name, "ok": False, "error": str(e)[:200]}


async def main(args):
    db = get_supabase()
    q = db.table("companies").select("name, priority").eq("is_target", True)
    if args.priority:
        q = q.eq("priority", args.priority)
    targets = q.execute().data or []
    if args.names:
        wanted = {n.strip().lower() for n in args.names.split(",")}
        targets = [t for t in targets if t["name"].lower() in wanted]

    print(f"Researching {len(targets)} target companies (concurrency={args.concurrency})")
    print(f"Force refresh: {args.force}\n")

    sem = asyncio.Semaphore(args.concurrency)

    async def gated(name: str):
        async with sem:
            print(f"  → {name}", flush=True)
            return await research_one(name, force=args.force)

    results = await asyncio.gather(*[gated(t["name"]) for t in targets])

    ok = [r for r in results if r["ok"]]
    fresh = [r for r in ok if r.get("status") == "fresh"]
    built = [r for r in ok if r.get("status") == "built"]
    failed = [r for r in results if not r["ok"]]

    print(f"\n{'═'*60}")
    print(f"✅ Complete: {len(ok)}/{len(results)} succeeded")
    print(f"   Built fresh: {len(built)}")
    print(f"   Already cached: {len(fresh)}")
    print(f"   Failed: {len(failed)}")
    if failed:
        print("\nFailures:")
        for f in failed[:10]:
            print(f"   ❌ {f['name']}: {f['error']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--priority", choices=["high", "medium", "low"], help="Only research one priority tier")
    parser.add_argument("--names", help="Comma-separated company names")
    parser.add_argument("--concurrency", type=int, default=3, help="Parallel companies (default 3)")
    parser.add_argument("--force", action="store_true", help="Re-research even if knowledge is fresh")
    args = parser.parse_args()
    asyncio.run(main(args))
