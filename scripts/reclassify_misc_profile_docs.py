"""
BUG-016: Re-run the upgraded heuristic classifier across every
`profile_source_document` row currently tagged `role_specific_resume` and
update the document_class IFF the new classifier disagrees.

Idempotent — only writes when there is a change. Safe to re-run.

Usage
-----
    cd jobHunt
    set -a && . ./.env && set +a
    python3 scripts/reclassify_misc_profile_docs.py [--dry-run]

The script does NOT recompute `profile_keyword` — the parent task notes
that re-derivation is needed but explicitly out of scope. Add a follow-up.
"""
from __future__ import annotations
import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

# Make project root importable when run as `python3 scripts/...`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agents.document_classifier import classify_document  # noqa: E402
from db.client import get_supabase  # noqa: E402

log = logging.getLogger("reclassify_misc_profile_docs")


def _read_extracted_text(file_path: str | None) -> str | None:
    """Best-effort: try to read the on-disk file's text content. The
    classifier degrades gracefully to filename-only signals if the file
    isn't reachable (almost always the case when run from CI / Railway)."""
    if not file_path:
        return None
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return None
    try:
        suf = p.suffix.lower()
        if suf in {".txt", ".md", ".html", ".json", ".csv"}:
            return p.read_text(encoding="utf-8", errors="ignore")
        if suf == ".docx":
            try:
                import docx  # python-docx
            except ImportError:
                return None
            try:
                d = docx.Document(str(p))
                return "\n".join(par.text for par in d.paragraphs)
            except Exception:
                return None
        if suf == ".pdf":
            # Try pypdf — not in requirements.txt; degrade gracefully
            try:
                import pypdf  # type: ignore
            except ImportError:
                return None
            try:
                reader = pypdf.PdfReader(str(p))
                return "\n".join(
                    (page.extract_text() or "") for page in reader.pages
                )
            except Exception:
                return None
    except Exception:
        return None
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print proposed changes but do not write to the DB.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )

    db = get_supabase()
    rows = (
        db.table("profile_source_document")
        .select("id, file_name, file_path, file_size, char_count, document_class")
        .eq("document_class", "role_specific_resume")
        .execute()
        .data
        or []
    )
    log.info("Pulled %d rows currently tagged role_specific_resume.", len(rows))

    changes: list[tuple[int, str, str, str]] = []
    by_new_class: Counter[str] = Counter()

    for row in rows:
        extracted = _read_extracted_text(row.get("file_path"))
        new_cls = classify_document(
            file_name=row["file_name"],
            file_path=row.get("file_path"),
            file_size=row.get("file_size"),
            char_count=row.get("char_count"),
            extracted_text=extracted,
        )
        if new_cls and new_cls != row["document_class"]:
            changes.append((row["id"], row["file_name"], row["document_class"], new_cls))
            by_new_class[new_cls] += 1

    log.info("Proposed updates: %d / %d", len(changes), len(rows))
    for cls, n in by_new_class.most_common():
        log.info("  %4d  %s", n, cls)

    if args.dry_run:
        log.info("--dry-run: not writing to the DB.")
        for id_, name, old, new in changes:
            print(f"  {id_:>4}  {old:>22}  ->  {new:<18}  {name}")
        return 0

    # Apply updates one at a time; the volume is tiny (<200 rows) so a
    # batched upsert isn't worth the complexity. We log each write.
    for id_, name, old, new in changes:
        try:
            db.table("profile_source_document").update(
                {"document_class": new}
            ).eq("id", id_).execute()
            log.info("id=%s '%s' %s -> %s", id_, name, old, new)
        except Exception as e:
            log.warning("id=%s update failed: %s", id_, e)

    log.info("Done. %d rows updated.", len(changes))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
