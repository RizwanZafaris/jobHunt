"""
Step 5: Seed profile_* tables in Supabase from the JSON outputs.
Reads SUPABASE_URL + SUPABASE_SERVICE_KEY from env (set by sourcing /tmp/jh-sb.env or shell).
"""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

try:
    from supabase import create_client
except ImportError:
    print("Run: pip install supabase")
    sys.exit(1)

OUT = Path("/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/job_hunt_v2/profile_build/output")

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_KEY env vars required")
    sys.exit(1)

db = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)


def seed_master():
    profile = json.loads((OUT / "master_profile.json").read_text())
    contact = profile.get("contact", {})
    row = {
        "id": 1,
        "name": profile["name"],
        "headline": profile.get("headline", ""),
        "summary": profile.get("summary", ""),
        "location": contact.get("location", ""),
        "email": contact.get("email", ""),
        "phones": contact.get("phones", []),
        "linkedin_url": contact.get("linkedin", ""),
        "core_competencies": profile.get("core_competencies", []),
        "technical_knowledge": profile.get("technical_knowledge", []),
        "languages": profile.get("languages", []),
        "ai_solutions": profile.get("ai_solutions", []),
        "tailored_resumes_count": profile.get("tailored_resumes_count", 0),
    }
    db.table("profile_master").upsert(row, on_conflict="id").execute()
    print(f"  ✅ profile_master seeded")

    # experience: clear + insert (sort_order is the source of truth)
    db.table("profile_experience").delete().neq("id", 0).execute()
    rows = []
    for i, role in enumerate(profile.get("experience", [])):
        rows.append({
            "sort_order": i,
            "title": role.get("title", ""),
            "company": role.get("company", ""),
            "location": role.get("location", ""),
            "scope": role.get("scope", ""),
            "dates": role.get("dates", ""),
            "summary": role.get("summary", ""),
            "highlights": role.get("highlights", []),
            "groups": role.get("groups", []),
        })
    if rows:
        db.table("profile_experience").insert(rows).execute()
    print(f"  ✅ profile_experience: {len(rows)} rows")

    # certifications
    db.table("profile_certification").delete().neq("id", 0).execute()
    cert_rows = []
    for i, c in enumerate(profile.get("certifications", [])):
        cert_rows.append({
            "name": c["name"],
            "full_name": c.get("full_name", ""),
            "sort_order": i,
        })
    if cert_rows:
        db.table("profile_certification").insert(cert_rows).execute()
    print(f"  ✅ profile_certification: {len(cert_rows)} rows")

    # education
    db.table("profile_education").delete().neq("id", 0).execute()
    edu_rows = []
    for i, e in enumerate(profile.get("education", [])):
        edu_rows.append({
            "title": e.get("title", e.get("raw", "")),
            "details": e.get("details", ""),
            "year": e.get("year", ""),
            "notes": e.get("notes", ""),
            "sort_order": i,
        })
    if edu_rows:
        db.table("profile_education").insert(edu_rows).execute()
    print(f"  ✅ profile_education: {len(edu_rows)} rows")


def seed_keywords():
    kw_data = json.loads((OUT / "keywords.json").read_text())

    # Clear existing
    db.table("profile_keyword").delete().neq("id", 0).execute()

    # Insert in batches of 200
    rows = []
    for k in kw_data["keywords"]:
        rows.append({
            "keyword": k["keyword"],
            "category": k["category"],
            "total_occurrences": k["total_occurrences"],
            "files_count": k["files_count"],
            "coverage_pct": k["coverage_pct"],
            "avg_per_file": k["avg_per_file"],
            "ats_strength": k["ats_strength"],
        })

    batch_size = 200
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        db.table("profile_keyword").upsert(batch, on_conflict="keyword,category").execute()
    print(f"  ✅ profile_keyword: {len(rows)} keywords")

    # Categories aggregate
    db.table("profile_keyword_category").delete().neq("category", "").execute()
    cat_rows = []
    for cat, s in kw_data["by_category"].items():
        cat_rows.append({
            "category": cat,
            "keyword_count": s["keyword_count"],
            "total_occurrences": s["total_occurrences"],
            "avg_strength": s["avg_strength"],
            "top_keywords": s["top_keywords"],
        })
    if cat_rows:
        db.table("profile_keyword_category").upsert(cat_rows, on_conflict="category").execute()
    print(f"  ✅ profile_keyword_category: {len(cat_rows)} rows")


def seed_sources():
    extracted = json.loads((OUT / "extracted.json").read_text())
    inv = json.loads((OUT / "inventory.json").read_text())
    by_hash = {e["hash"]: e for e in inv}

    db.table("profile_source_document").delete().neq("id", 0).execute()
    rows = []
    from datetime import datetime, timezone
    for h, meta in extracted.items():
        inv_e = by_hash.get(h, {})
        mtime_iso = None
        try:
            mtime_iso = datetime.fromtimestamp(float(meta["mtime"]), tz=timezone.utc).isoformat()
        except Exception:
            pass
        rows.append({
            "file_hash": h,
            "file_path": meta["path"],
            "file_name": meta["name"],
            "document_class": meta["class"],
            "char_count": meta["char_count"],
            "file_size": meta.get("size", 0),
            "source_mtime": mtime_iso,
            "is_duplicate": False,
        })
    # Insert in batches of 50
    for i in range(0, len(rows), 50):
        db.table("profile_source_document").upsert(rows[i:i + 50], on_conflict="file_hash").execute()
    print(f"  ✅ profile_source_document: {len(rows)} rows")


def main():
    print("Seeding profile_* tables...")
    seed_master()
    seed_keywords()
    seed_sources()
    print("\n✅ All seeded")


if __name__ == "__main__":
    main()
