"""
Step 1: Walk all candidate dirs, classify each file, hash for dedup, write inventory JSON.
Output: profile_build/output/inventory.json

⚠️  DEPRECATED 2026-05-12 (Tier 1 follow-up to BUG-016)
─────────────────────────────────────────────────────────
This is the historical one-shot inventory + filename-based classifier from
the initial profile build. **`classify()` in this file is NOT the canonical
classifier.**

The canonical classifier is `agents/document_classifier.py` — added in the
BUG-016 sweep — which uses content-based heuristics (header detection,
opening-word patterns, quarter headings, char/file-size thresholds) and
recognises five new classes: `cover_letter`, `job_description`, `roadmap`,
`interview_prep`, `misc_short`, plus a `needs_review` ambiguous tier.

This file is kept ONLY for git history / reproducibility of the original
seed run. Do not extend the `classify()` function here — extend
`agents/document_classifier.py::classify_document()` instead, then re-run
`scripts/reclassify_misc_profile_docs.py` if the change affects existing
rows.

If you find a path in production that still calls into this module's
`classify()`, raise a follow-up bug (BUG-037+) so we can migrate it.
─────────────────────────────────────────────────────────
"""
from __future__ import annotations
import hashlib
import json
import os
import re
from pathlib import Path

ROOTS = [
    "/Users/rizwanzafar/Desktop/Resume",
    "/Users/rizwanzafar/Desktop/LinkedIn_Career",
    "/Users/rizwanzafar/Desktop/Desktop_Backup/Resume",
]
DESKTOP_CV_PDFS = sorted(Path("/Users/rizwanzafar/Desktop").glob("cv-*.pdf"))

OUT = Path("/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/job_hunt_v2/profile_build/output")
OUT.mkdir(parents=True, exist_ok=True)

# Skip these clearly irrelevant
SKIP_DIRS = {"Bank_Statements", "Legal_Docs", "09_From_Other_Sessions"}
SKIP_NAME_PATTERNS = [
    r"^\.DS_Store$",
    r"^~\$",  # Word lock files
    r"^FELO_",  # FELO project artifacts
    r"^Felo",
    r"\.apk$",
    r"^IS-Incident",
    r"^DISBURSED",
    r"^INTEGRATION",
    r"^API Integration",
    r"^Frontend Attestation",
    r"^__\s",
    r"^Boku",
    r"^RA01-",  # legal scan
    r"^CNIC",
    r"^Passport",
    r"^Degree Back",
    r"^Untitled",
    r"^Back CNIC",
    r"^front\.jpg$",
    r"^\d+\.jpg$",
]
EXTS = {".pdf", ".docx", ".doc", ".md", ".txt", ".html", ".json", ".csv"}


def should_skip(path: Path) -> bool:
    name = path.name
    if any(re.search(p, name) for p in SKIP_NAME_PATTERNS):
        return True
    parts = set(path.parts)
    if parts & SKIP_DIRS:
        return True
    if path.suffix.lower() not in EXTS:
        return True
    return False


def classify(path: Path) -> str:
    """Best-guess classification from path + filename."""
    name_l = path.name.lower()
    parts = [p.lower() for p in path.parts]

    # Master / general
    if "master_cv" in name_l or "master cv" in name_l:
        return "master_cv"
    if "executive_resume" in name_l:
        return "executive_resume"

    # LinkedIn-specific
    if "linkedin_headline" in name_l or "linkedin_about" in name_l or "headline_about" in name_l:
        return "linkedin_headline_about"
    if "linkedin_optimization" in name_l or "linkedin_top1" in name_l or "linkedin_full_optimization" in name_l:
        return "linkedin_optimization"
    if "linkedin_audit" in name_l:
        return "linkedin_audit"
    if "linkedin-post" in name_l or "linkedin_post" in name_l:
        return "linkedin_post"
    if "linkedin_articles" in name_l or "fintech_linkedin" in name_l:
        return "linkedin_article"

    # Application content
    if "cover_letter" in name_l:
        return "cover_letter"
    if "application_answer" in name_l or "application_answers" in name_l:
        return "application_answer"
    if "interview_prep" in name_l or "interview_master" in name_l or "qa_bank" in name_l or "complete_prep" in name_l:
        return "interview_prep"
    if "assessment" in name_l or "deep_assessment" in name_l:
        return "company_assessment"
    if "recruiter_analysis" in name_l:
        return "recruiter_analysis"
    if "target_roles" in name_l:
        return "target_roles"

    # Job search reports
    if "job_search" in name_l or "job-scan" in name_l or "daily-scan" in name_l or "daily_job_scan" in name_l:
        return "job_search_report"

    # Org / current employer docs
    if "simpaisa" in name_l and ("blueprint" in name_l or "operating" in name_l or "transformation" in name_l or "roadmap" in name_l or "orgdesign" in name_l):
        return "simpaisa_org_doc"
    if "simpaisa" in name_l and "design" in name_l:
        return "simpaisa_tech_doc"

    # Role-specific resumes — check filename for company/role keywords
    role_keywords = [
        "visa", "mastercard", "tabby", "wise", "reap", "revolut", "worldpay", "talabat",
        "myzoi", "stanchart", "bloomberg", "axian", "hala", "salla", "terrapay", "mai", "mc",
        "mvpc", "mcpo", "tpm", "mdes", "cpo", "hop", "head_of_product", "head_of_projects",
        "senior_pm", "head_payments", "agile_product_owner", "manager_po", "vca", "issuer",
        "gen_ai", "genai", "ai_consultant", "analytics_ai", "product_portfolio",
        "services_bd", "vp_commerce", "strategy", "consulting", "founding_cpo",
        "program_project", "product_manager", "director_sme", "senior_program",
        "digital_banking", "digital_cards", "digital_transformation", "salt", "mn",
        "network_international", "vca_digital", "issuer_processing", "n_international"
    ]
    if path.suffix.lower() in {".pdf", ".docx"}:
        if any(k in name_l.replace("-", "_").replace(" ", "_") for k in role_keywords):
            return "role_specific_resume"
        # Generic resumes_general dir → general resume
        if "resumes_general" in parts:
            return "general_resume"
        # If filename is just rizwan_zafar.docx or similar
        if re.match(r"^rizwan[_\s]zafar\.?(docx|pdf|_)?", name_l) or "rizwan zafar" in name_l:
            return "general_resume"

    # Misc useful
    if path.suffix.lower() == ".md" and "rizwan" in name_l:
        return "rizwan_markdown"

    return "other"


def file_hash(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()[:16]
    except Exception:
        return "ERR"


def walk():
    seen_paths = set()
    files = []
    for root in ROOTS:
        for p in Path(root).rglob("*"):
            if not p.is_file():
                continue
            if should_skip(p):
                continue
            if p in seen_paths:
                continue
            seen_paths.add(p)
            files.append(p)
    for p in DESKTOP_CV_PDFS:
        if p not in seen_paths:
            files.append(p)
    return files


def main():
    files = walk()
    print(f"Found {len(files)} candidate files")

    inventory = []
    by_hash: dict[str, list[str]] = {}
    for p in files:
        cls = classify(p)
        h = file_hash(p)
        try:
            stat = p.stat()
            size = stat.st_size
            mtime = stat.st_mtime
        except Exception:
            size, mtime = 0, 0
        entry = {
            "path": str(p),
            "name": p.name,
            "ext": p.suffix.lower(),
            "size": size,
            "mtime": mtime,
            "class": cls,
            "hash": h,
        }
        inventory.append(entry)
        by_hash.setdefault(h, []).append(str(p))

    # Mark dedup groups: same content hash
    dups = {h: paths for h, paths in by_hash.items() if len(paths) > 1}
    for entry in inventory:
        entry["is_duplicate"] = entry["hash"] in dups
        entry["dedup_group"] = entry["hash"] if entry["is_duplicate"] else None

    # Stats
    by_class: dict[str, int] = {}
    for e in inventory:
        by_class[e["class"]] = by_class.get(e["class"], 0) + 1

    print("By class:")
    for c, n in sorted(by_class.items(), key=lambda x: -x[1]):
        print(f"  {n:4d}  {c}")
    print(f"\nDuplicate groups: {len(dups)}")
    print(f"Total files in dup groups: {sum(len(v) for v in dups.values())}")

    out_path = OUT / "inventory.json"
    out_path.write_text(json.dumps(inventory, indent=2, default=str))
    print(f"\n✅ Wrote {out_path}")


if __name__ == "__main__":
    main()
