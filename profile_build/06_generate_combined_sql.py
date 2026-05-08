
"""
Generate one self-contained SQL file: schema + all seed data.
User pastes this into Supabase SQL editor and clicks Run.
Output: profile_build/output/profile_setup.sql
"""
from __future__ import annotations
import json
from pathlib import Path

OUT = Path("/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/job_hunt_v2/profile_build/output")
SCHEMA = Path("/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/job_hunt_v2/db/profile_schema.sql")


def sql_str(s: str | None) -> str:
    if s is None:
        return "NULL"
    return "'" + str(s).replace("'", "''") + "'"


def sql_text_array(items: list) -> str:
    if not items:
        return "ARRAY[]::TEXT[]"
    inner = ",".join(sql_str(x) for x in items)
    return f"ARRAY[{inner}]::TEXT[]"


def sql_jsonb(obj) -> str:
    s = json.dumps(obj, default=str)
    return sql_str(s) + "::jsonb"


def gen_master(profile: dict) -> str:
    contact = profile.get("contact", {})
    return f"""
INSERT INTO profile_master (
    id, name, headline, summary, location, email, phones, linkedin_url,
    core_competencies, technical_knowledge, languages, ai_solutions, tailored_resumes_count
) VALUES (
    1,
    {sql_str(profile["name"])},
    {sql_str(profile.get("headline", ""))},
    {sql_str(profile.get("summary", ""))},
    {sql_str(contact.get("location", ""))},
    {sql_str(contact.get("email", ""))},
    {sql_text_array(contact.get("phones", []))},
    {sql_str(contact.get("linkedin", ""))},
    {sql_text_array(profile.get("core_competencies", []))},
    {sql_text_array(profile.get("technical_knowledge", []))},
    {sql_jsonb(profile.get("languages", []))},
    {sql_jsonb(profile.get("ai_solutions", []))},
    {profile.get("tailored_resumes_count", 0)}
)
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name, headline = EXCLUDED.headline, summary = EXCLUDED.summary,
    location = EXCLUDED.location, email = EXCLUDED.email, phones = EXCLUDED.phones,
    linkedin_url = EXCLUDED.linkedin_url, core_competencies = EXCLUDED.core_competencies,
    technical_knowledge = EXCLUDED.technical_knowledge, languages = EXCLUDED.languages,
    ai_solutions = EXCLUDED.ai_solutions, tailored_resumes_count = EXCLUDED.tailored_resumes_count;
"""


def gen_experience(profile: dict) -> str:
    out = ["DELETE FROM profile_experience;"]
    for i, role in enumerate(profile.get("experience", [])):
        out.append(f"""
INSERT INTO profile_experience (sort_order, title, company, location, scope, dates, summary, highlights, groups)
VALUES (
    {i},
    {sql_str(role.get("title", ""))},
    {sql_str(role.get("company", ""))},
    {sql_str(role.get("location", ""))},
    {sql_str(role.get("scope", ""))},
    {sql_str(role.get("dates", ""))},
    {sql_str(role.get("summary", ""))},
    {sql_text_array(role.get("highlights", []))},
    {sql_jsonb(role.get("groups", []))}
);""")
    return "\n".join(out)


def gen_certs(profile: dict) -> str:
    out = ["DELETE FROM profile_certification;"]
    for i, c in enumerate(profile.get("certifications", [])):
        out.append(f"""
INSERT INTO profile_certification (name, full_name, sort_order)
VALUES ({sql_str(c["name"])}, {sql_str(c.get("full_name", ""))}, {i})
ON CONFLICT (name) DO NOTHING;""")
    return "\n".join(out)


def gen_education(profile: dict) -> str:
    out = ["DELETE FROM profile_education;"]
    for i, e in enumerate(profile.get("education", [])):
        out.append(f"""
INSERT INTO profile_education (title, details, year, notes, sort_order)
VALUES (
    {sql_str(e.get("title", e.get("raw", "")))},
    {sql_str(e.get("details", ""))},
    {sql_str(e.get("year", ""))},
    {sql_str(e.get("notes", ""))},
    {i}
);""")
    return "\n".join(out)


def gen_keywords(kw_data: dict) -> str:
    out = ["DELETE FROM profile_keyword;"]
    rows = []
    for k in kw_data["keywords"]:
        rows.append(
            f"({sql_str(k['keyword'])}, {sql_str(k['category'])}, "
            f"{k['total_occurrences']}, {k['files_count']}, {k['coverage_pct']}, "
            f"{k['avg_per_file']}, {k['ats_strength']})"
        )
    # batch in chunks of 100 inserts each
    for i in range(0, len(rows), 100):
        out.append(
            "INSERT INTO profile_keyword (keyword, category, total_occurrences, files_count, coverage_pct, avg_per_file, ats_strength) VALUES\n"
            + ",\n".join(rows[i:i + 100])
            + "\nON CONFLICT (keyword, category) DO UPDATE SET "
            + "total_occurrences = EXCLUDED.total_occurrences, files_count = EXCLUDED.files_count, "
            + "coverage_pct = EXCLUDED.coverage_pct, avg_per_file = EXCLUDED.avg_per_file, "
            + "ats_strength = EXCLUDED.ats_strength;"
        )
    return "\n\n".join(out)


def gen_categories(kw_data: dict) -> str:
    out = ["DELETE FROM profile_keyword_category;"]
    for cat, s in kw_data["by_category"].items():
        out.append(f"""
INSERT INTO profile_keyword_category (category, keyword_count, total_occurrences, avg_strength, top_keywords)
VALUES (
    {sql_str(cat)}, {s["keyword_count"]}, {s["total_occurrences"]}, {s["avg_strength"]},
    {sql_text_array(s["top_keywords"])}
)
ON CONFLICT (category) DO UPDATE SET
    keyword_count = EXCLUDED.keyword_count,
    total_occurrences = EXCLUDED.total_occurrences,
    avg_strength = EXCLUDED.avg_strength,
    top_keywords = EXCLUDED.top_keywords;""")
    return "\n".join(out)


def gen_sources(extracted: dict) -> str:
    out = ["DELETE FROM profile_source_document;"]
    rows = []
    from datetime import datetime, timezone
    for h, meta in extracted.items():
        try:
            mtime_iso = datetime.fromtimestamp(float(meta["mtime"]), tz=timezone.utc).isoformat()
            mtime = sql_str(mtime_iso)
        except Exception:
            mtime = "NULL"
        rows.append(
            f"({sql_str(h)}, {sql_str(meta['path'])}, {sql_str(meta['name'])}, "
            f"{sql_str(meta['class'])}, {meta['char_count']}, {meta.get('size', 0)}, "
            f"{mtime}, FALSE)"
        )
    for i in range(0, len(rows), 50):
        out.append(
            "INSERT INTO profile_source_document (file_hash, file_path, file_name, document_class, char_count, file_size, source_mtime, is_duplicate) VALUES\n"
            + ",\n".join(rows[i:i + 50])
            + "\nON CONFLICT (file_hash) DO NOTHING;"
        )
    return "\n\n".join(out)


def main():
    profile = json.loads((OUT / "master_profile.json").read_text())
    kw_data = json.loads((OUT / "keywords.json").read_text())
    extracted = json.loads((OUT / "extracted.json").read_text())

    schema = SCHEMA.read_text()

    parts = [
        "-- ========================================================================",
        "-- Phase A: Profile Foundation — combined schema + seed",
        "-- Paste into Supabase SQL editor and click Run.",
        "-- Idempotent: safe to re-run.",
        "-- ========================================================================",
        "",
        schema,
        "",
        "-- ── seed: profile_master ─────────────────────────────────────────────────",
        gen_master(profile),
        "",
        "-- ── seed: profile_experience ─────────────────────────────────────────────",
        gen_experience(profile),
        "",
        "-- ── seed: profile_certification ──────────────────────────────────────────",
        gen_certs(profile),
        "",
        "-- ── seed: profile_education ──────────────────────────────────────────────",
        gen_education(profile),
        "",
        "-- ── seed: profile_keyword ────────────────────────────────────────────────",
        gen_keywords(kw_data),
        "",
        "-- ── seed: profile_keyword_category ───────────────────────────────────────",
        gen_categories(kw_data),
        "",
        "-- ── seed: profile_source_document ────────────────────────────────────────",
        gen_sources(extracted),
        "",
        "-- ── reload PostgREST schema cache ────────────────────────────────────────",
        "NOTIFY pgrst, 'reload schema';",
    ]

    out_path = OUT / "profile_setup.sql"
    out_path.write_text("\n".join(parts))
    size = out_path.stat().st_size
    print(f"✅ Wrote {out_path}")
    print(f"   Size: {size:,} bytes ({size/1024:.1f} KB)")
    print(f"   Master: 1 row")
    print(f"   Experience: {len(profile.get('experience', []))} rows")
    print(f"   Certifications: {len(profile.get('certifications', []))}")
    print(f"   Education: {len(profile.get('education', []))}")
    print(f"   Keywords: {len(kw_data['keywords'])}")
    print(f"   Categories: {len(kw_data['by_category'])}")
    print(f"   Source documents: {len(extracted)}")


if __name__ == "__main__":
    main()
