"""
agents/persona_news_check.py — P1.3 weekly recency cron logic.

For each (user, company) pair this module:
  1. Reads the user's company_personas row.
  2. Asks Perplexity Sonar "what's new at $COMPANY in the last 30 days?"
  3. Filters citations to those newer than persona.last_news_check_at.
  4. Inserts each new citation into company_knowledge under
     section="news_recent" so the next Apify deep-scrape (or G2 insider
     expert RAG hop) sees the fresh anchor.
  5. Stamps persona.last_news_check_at + bumps news_check_count.

Apify stays the canonical deep-scrape pipeline; Perplexity is the recency
trigger that decides when an Apify re-scrape is worth firing.

CLI:
    python -m agents.persona_news_check --company "Marqeta" --user-id <uuid>
    python -m agents.persona_news_check --user-id <uuid> --all

The --all path runs every persona under the user with concurrency=3.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from agents import perplexity_search
from db.client import get_supabase, upsert_company_knowledge

logger = logging.getLogger(__name__)

# Cap concurrent Perplexity calls so we don't trip 429s on the free tier.
DEFAULT_CONCURRENCY = 3


def _parse_published_at(raw: Any) -> Optional[datetime]:
    """Best-effort ISO-8601 parse. Returns None on anything weird."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        # tolerate trailing 'Z'
        cleaned = raw.replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _is_fresh(citation: dict, since: Optional[datetime]) -> bool:
    """Citation counts as new if it's newer than `since` OR we can't tell."""
    if since is None:
        return True
    pub = _parse_published_at(citation.get("published_at"))
    if pub is None:
        # Unknown date — treat as fresh; better to over-store than miss anchors.
        return True
    return pub > since


async def _persist_knowledge_row(
    *,
    user_id: UUID,
    company_name: str,
    citation: dict,
    summary: str,
) -> bool:
    """Insert one row into company_knowledge for a fresh citation.

    Prefers the existing upsert helper (which embeds + upserts on
    (company_name, section)). Falls back to a direct insert if the
    helper raises — we don't want one bad citation to blow the batch.
    """
    snippet = citation.get("snippet") or citation.get("title") or ""
    # The helper upserts on (company_name, section) — to keep multiple
    # news rows under "news_recent" without overwriting, namespace the
    # section by URL hash. Embedding is still useful for RAG.
    url = citation.get("url") or ""
    short_hash = abs(hash(url)) % (10**8)
    section = f"news_recent_{short_hash:08d}"

    # Compose the content the embedder will see. Front-loads URL + title
    # so semantic search can match it on either.
    content = (
        f"[news] {citation.get('title') or '(untitled)'}\n"
        f"url: {url}\n"
        f"published_at: {citation.get('published_at') or 'unknown'}\n\n"
        f"{snippet or summary[:500]}"
    )
    metadata = {
        "source": "perplexity_recency",
        "user_id": str(user_id),
        "url": url,
        "title": citation.get("title"),
        "published_at": citation.get("published_at"),
    }

    try:
        await upsert_company_knowledge(
            company_name=company_name,
            company_id=None,
            section=section,
            content=content,
            source_url=url or None,
            metadata=metadata,
        )
        return True
    except Exception as exc:
        logger.warning("upsert_company_knowledge failed for %s/%s: %r — falling back to direct insert", company_name, url, exc)
        try:
            db = get_supabase()
            db.table("company_knowledge").insert({
                "user_id": str(user_id),
                "company_name": company_name,
                "section": section,
                "content": content,
                "source_url": url or None,
                "metadata": metadata,
            }).execute()
            return True
        except Exception:
            logger.exception("direct insert into company_knowledge also failed")
            return False


async def check_company_news(user_id: UUID, company_name: str) -> dict[str, Any]:
    """Run a single recency check + persist new anchors for one company."""
    db = get_supabase()
    persona_row = (
        db.table("company_personas")
        .select("id, user_id, company_name, last_news_check_at, news_check_count")
        .eq("user_id", str(user_id))
        .eq("company_name", company_name)
        .limit(1)
        .execute()
    ).data
    if not persona_row:
        logger.info("no persona for user=%s company=%s — skipping recency check", user_id, company_name)
        return {
            "error": "no persona",
            "company": company_name,
            "new_anchors": 0,
            "new_knowledge_rows": 0,
            "cost_usd": 0.0,
            "citations": [],
        }

    persona = persona_row[0]
    last_check = _parse_published_at(persona.get("last_news_check_at"))

    result = await perplexity_search.recency_check(company_name)
    citations = result.get("citations") or []
    fresh = [c for c in citations if _is_fresh(c, last_check)]

    persisted = 0
    for cit in fresh:
        ok = await _persist_knowledge_row(
            user_id=user_id,
            company_name=company_name,
            citation=cit,
            summary=result.get("summary") or "",
        )
        if ok:
            persisted += 1

    # Stamp persona regardless of insert outcome — the call happened.
    now_iso = datetime.now(timezone.utc).isoformat()
    new_count = int(persona.get("news_check_count") or 0) + 1
    try:
        db.table("company_personas").update({
            "last_news_check_at": now_iso,
            "news_check_count": new_count,
        }).eq("id", persona["id"]).execute()
    except Exception:
        logger.exception("failed to stamp last_news_check_at on persona %s", persona["id"])

    return {
        "company": company_name,
        "new_anchors": len(fresh),
        "new_knowledge_rows": persisted,
        "cost_usd": float(result.get("cost_usd") or 0.0),
        "citations": fresh,
        "summary": result.get("summary"),
    }


async def batch_check_all_personas(user_id: UUID, concurrency: int = DEFAULT_CONCURRENCY) -> list[dict[str, Any]]:
    """Recency-check every persona owned by `user_id`."""
    db = get_supabase()
    rows = (
        db.table("company_personas")
        .select("company_name")
        .eq("user_id", str(user_id))
        .order("last_news_check_at", desc=False, nullsfirst=True)
        .execute()
    ).data or []

    sem = asyncio.Semaphore(max(1, concurrency))

    async def _bound(company: str) -> dict[str, Any]:
        async with sem:
            try:
                return await check_company_news(user_id, company)
            except Exception as exc:
                logger.exception("check_company_news failed for %s", company)
                return {
                    "company": company,
                    "error": repr(exc),
                    "new_anchors": 0,
                    "new_knowledge_rows": 0,
                    "cost_usd": 0.0,
                    "citations": [],
                }

    results = await asyncio.gather(*[_bound(r["company_name"]) for r in rows])
    total_cost = round(sum(float(r.get("cost_usd") or 0.0) for r in results), 6)
    logger.info(
        "batch_check_all_personas user=%s n=%d total_cost_usd=%.4f",
        user_id, len(results), total_cost,
    )
    return results


# ─── CLI ──────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="P1.3 persona recency check via Perplexity")
    p.add_argument("--user-id", required=True, help="UUID of the owning user")
    p.add_argument("--company", help="Single company name to check")
    p.add_argument("--all", action="store_true", help="Check every persona for the user")
    p.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    return p.parse_args()


def _main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    args = _parse_args()
    try:
        user_uuid = UUID(args.user_id)
    except ValueError:
        raise SystemExit("--user-id must be a valid UUID")

    if args.all:
        results = asyncio.run(batch_check_all_personas(user_uuid, args.concurrency))
        total = sum(float(r.get("cost_usd") or 0.0) for r in results)
        new_rows = sum(int(r.get("new_knowledge_rows") or 0) for r in results)
        print(f"checked {len(results)} personas — {new_rows} new knowledge rows — total cost ${total:.4f}")
        return

    if not args.company:
        raise SystemExit("provide --company <name> or --all")
    out = asyncio.run(check_company_news(user_uuid, args.company))
    print(out)


if __name__ == "__main__":
    _main()
