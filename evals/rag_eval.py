"""
evals/rag_eval.py — RAG retrieval evaluator.

Calls db.client.search_company_knowledge for each labelled query and
computes recall@5, recall@10, and MRR against the ground-truth
relevant_doc_ids.

Until the labels in evals/rag_queries.json are populated, this script
emits a warning and returns the raw retrieval order so a human can
hand-label.

CLI:
    python -m evals.rag_eval --queries evals/rag_queries.json
    python -m evals.rag_eval --queries evals/rag_queries.json --k 10 --out evals/reports/

Output: evals/reports/rag_{ISO_timestamp}.json + stdout summary.

Recall@k:
    Of the labelled relevant docs for this query, how many appear in
    the top-k retrieved? Reported as a fraction.

MRR (Mean Reciprocal Rank):
    For each query, find the rank (1-indexed) of the FIRST relevant
    doc in the retrieved list. RR = 1/rank, or 0 if no relevant doc
    in the top-k. MRR = mean of RR across all queries.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUERIES = REPO_ROOT / "evals" / "rag_queries.json"
DEFAULT_REPORTS_DIR = REPO_ROOT / "evals" / "reports"


# ─── Metrics ──────────────────────────────────────────────────────────────
def recall_at_k(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    """Fraction of relevant docs appearing in top-k retrieved."""
    if not relevant_ids:
        return 0.0
    top = set(retrieved_ids[:k])
    hits = sum(1 for r in relevant_ids if r in top)
    return round(hits / len(relevant_ids), 3)


def reciprocal_rank(retrieved_ids: list[str], relevant_ids: list[str], k: int) -> float:
    """1/rank of first relevant doc in top-k retrieved, else 0."""
    if not relevant_ids:
        return 0.0
    relevant_set = set(relevant_ids)
    for i, doc_id in enumerate(retrieved_ids[:k], start=1):
        if doc_id in relevant_set:
            return round(1.0 / i, 4)
    return 0.0


# ─── Per-query runner ────────────────────────────────────────────────────
async def evaluate_query(query_row: dict, k: int) -> dict:
    """
    Run one query against the live company_knowledge index. Returns:
      {
        "id", "query", "company",
        "retrieved": [{"id": ..., "section": ..., "score": ...}, ...],
        "metrics": {"recall@5", "recall@10", "rr@k"},
        "labelled": bool (True if relevant_doc_ids was non-empty)
      }
    """
    from db.client import search_company_knowledge

    rows = await search_company_knowledge(
        company_name=query_row["company"],
        query=query_row["query"],
        match_count=k,
    )

    # Try to extract a stable doc id from each row. company_knowledge schema
    # varies — section + content_hash makes the most stable composite key.
    retrieved: list[dict] = []
    for row in rows or []:
        doc_id = (
            str(row.get("id"))
            if row.get("id") is not None
            else f"{row.get('company_name', '')}::{row.get('section', '')}"
        )
        retrieved.append({
            "id": doc_id,
            "section": row.get("section"),
            "score": row.get("similarity") or row.get("score"),
            "content_preview": (row.get("content") or "")[:200],
        })

    retrieved_ids = [r["id"] for r in retrieved]
    relevant_ids = query_row.get("relevant_doc_ids") or []
    labelled = bool(relevant_ids)

    metrics: dict[str, float] = {}
    if labelled:
        metrics["recall@5"] = recall_at_k(retrieved_ids, relevant_ids, 5)
        metrics["recall@10"] = recall_at_k(retrieved_ids, relevant_ids, 10)
        metrics[f"rr@{k}"] = reciprocal_rank(retrieved_ids, relevant_ids, k)

    return {
        "id": query_row["id"],
        "query": query_row["query"],
        "company": query_row["company"],
        "retrieved": retrieved,
        "metrics": metrics,
        "labelled": labelled,
    }


# ─── Aggregator ──────────────────────────────────────────────────────────
def aggregate(results: list[dict], k: int) -> dict:
    """Mean recall@5, recall@10, MRR over labelled queries only."""
    labelled = [r for r in results if r["labelled"]]
    n = len(labelled)
    if n == 0:
        return {
            "n_queries": len(results),
            "n_labelled": 0,
            "warning": (
                "No queries are labelled with relevant_doc_ids. The metrics are "
                "meaningless until labels are populated. See evals/README.md §RAG."
            ),
        }
    return {
        "n_queries": len(results),
        "n_labelled": n,
        "mean_recall@5": round(
            sum(r["metrics"]["recall@5"] for r in labelled) / n, 3
        ),
        "mean_recall@10": round(
            sum(r["metrics"]["recall@10"] for r in labelled) / n, 3
        ),
        "MRR": round(
            sum(r["metrics"][f"rr@{k}"] for r in labelled) / n, 4
        ),
    }


# ─── Reporting ───────────────────────────────────────────────────────────
def write_report(out_dir: Path, payload: dict) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = out_dir / f"rag_{ts}.json"
    with out_path.open("w") as f:
        json.dump(payload, f, indent=2)
    return out_path


def print_summary(results: list[dict], overall: dict, out_path: Path) -> None:
    print(f"\nRAG eval — {overall.get('n_queries', 0)} queries "
          f"({overall.get('n_labelled', 0)} labelled)")
    if overall.get("warning"):
        print(f"  WARNING: {overall['warning']}")
    for r in results:
        if r["labelled"]:
            m = r["metrics"]
            print(
                f"  {r['id']:<8} {r['company']:<10} "
                f"r@5={m['recall@5']:.2f} r@10={m['recall@10']:.2f} "
                f"rr={m[next(k for k in m if k.startswith('rr@'))]:.3f}"
            )
        else:
            print(
                f"  {r['id']:<8} {r['company']:<10} (unlabelled — "
                f"top doc: {(r['retrieved'][:1] or [{'section': '∅'}])[0]['section']})"
            )
    if "mean_recall@5" in overall:
        print(
            f"\n  Overall: recall@5={overall['mean_recall@5']:.3f}, "
            f"recall@10={overall['mean_recall@10']:.3f}, "
            f"MRR={overall['MRR']:.4f}"
        )
    print(f"Report: {out_path}")


# ─── Entry point ─────────────────────────────────────────────────────────
async def amain(args) -> int:
    queries_path = Path(args.queries)
    if not queries_path.exists():
        print(f"Queries file not found: {queries_path}", file=sys.stderr)
        return 2
    with queries_path.open() as f:
        queries = json.load(f)

    results: list[dict] = []
    for q in queries:
        try:
            r = await evaluate_query(q, k=args.k)
        except Exception as e:
            logger.exception(f"Query {q.get('id')} failed")
            r = {
                "id": q.get("id"),
                "query": q.get("query"),
                "company": q.get("company"),
                "error": f"{type(e).__name__}: {e}",
                "labelled": bool(q.get("relevant_doc_ids")),
                "retrieved": [],
                "metrics": {},
            }
        results.append(r)

    overall = aggregate([r for r in results if "error" not in r], k=args.k)
    payload = {
        "kind": "rag_eval",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "k": args.k,
        "queries_file": str(queries_path),
        "results": results,
        "overall": overall,
    }
    out_path = write_report(args.out, payload)
    print_summary(results, overall, out_path)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run RAG retrieval eval")
    parser.add_argument(
        "--queries", default=str(DEFAULT_QUERIES),
        help="Path to rag_queries.json",
    )
    parser.add_argument(
        "--k", type=int, default=10,
        help="Top-k to retrieve per query (default 10)",
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_REPORTS_DIR,
        help="Output directory for the JSON report",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    return asyncio.run(amain(args))


if __name__ == "__main__":
    sys.exit(main())
