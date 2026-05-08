"""
Step 3: Parse Master_CV.md into structured master_profile.json.
The markdown is well-structured so we can do this deterministically.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

BUILD = Path("/Users/rizwanzafar/Desktop/Desktop_Backup/Resume/job_hunt_v2/profile_build")
OUT = BUILD / "output"
MASTER_CV = "/Users/rizwanzafar/Desktop/LinkedIn_Career/Linkedin/Rizwan_Zafar_Master_CV.md"


def parse():
    text = Path(MASTER_CV).read_text()

    profile: dict = {
        "name": "",
        "headline": "Product & Program Leader · Payments · Fintech · Cross-Border Systems",
        "contact": {},
        "summary": "",
        "core_competencies": [],
        "ai_solutions": [],
        "experience": [],
        "education": [],
        "certifications": [],
        "technical_knowledge": [],
        "languages": [],
        "tailored_resumes_count": 0,
    }

    lines = text.splitlines()
    i = 0

    # Name (first H1)
    while i < len(lines) and not lines[i].startswith("# "):
        i += 1
    profile["name"] = lines[i].lstrip("# ").strip()
    i += 1

    # Contact line (next non-empty line with separators)
    while i < len(lines) and not lines[i].strip():
        i += 1
    contact_line = lines[i].strip()
    # "**Dubai, UAE** | +971-58-9683970 | +92-321-2027229 | rizwanzaffar.pk@gmail.com | [linkedin.com/...]"
    parts = [p.strip().strip("*") for p in contact_line.split("|")]
    contact = {
        "location": parts[0] if parts else "",
        "phones": [p for p in parts[1:3] if re.match(r"^\+?\d", p)],
        "email": next((p for p in parts if "@" in p), ""),
        "linkedin": "",
    }
    for p in parts:
        m = re.search(r"\[([^\]]+)\]\(([^)]+)\)", p)
        if m and "linkedin" in m.group(1).lower():
            contact["linkedin"] = m.group(2)
    profile["contact"] = contact

    # Section parser: split on H2 headers
    sections: dict = {}
    current = None
    buffer = []
    for ln in lines:
        if ln.startswith("## "):
            if current:
                sections[current] = "\n".join(buffer).strip()
            current = ln.lstrip("# ").strip()
            buffer = []
        else:
            buffer.append(ln)
    if current:
        sections[current] = "\n".join(buffer).strip()

    # Executive Summary
    summary = sections.get("Executive Summary", "").strip()
    summary = re.sub(r"\n{2,}", " ", summary)
    summary = re.sub(r"\s+", " ", summary)
    profile["summary"] = summary

    # Core Competencies — bullet list
    comp_raw = sections.get("Core Competencies", "")
    profile["core_competencies"] = [
        ln.lstrip("- ").strip() for ln in comp_raw.splitlines() if ln.strip().startswith("-")
    ]

    # GenAI & AI Solutions
    ai_raw = sections.get("GenAI & AI — Deployed Production Solutions", "")
    # Each solution is **Title:** description
    ai_solutions = []
    for m in re.finditer(r"\*\*([^*]+):\*\*\s*([^\n]+(?:\n(?!\*\*)[^\n]+)*)", ai_raw):
        ai_solutions.append({"title": m.group(1).strip(), "description": m.group(2).strip()})
    profile["ai_solutions"] = ai_solutions

    # Professional Experience — H3 headers per role
    exp_raw = sections.get("Professional Experience", "")
    # Split by ### headers
    role_chunks = re.split(r"(?=^### )", exp_raw, flags=re.MULTILINE)
    role_chunks = [c.strip() for c in role_chunks if c.strip()]

    for chunk in role_chunks:
        ch_lines = chunk.splitlines()
        if not ch_lines or not ch_lines[0].startswith("### "):
            continue
        role = {"title": "", "company": "", "location": "", "dates": "", "summary": "", "highlights": [], "groups": []}
        role["title"] = ch_lines[0].lstrip("# ").strip()
        # Next bold line: company info
        for j, ln in enumerate(ch_lines[1:], 1):
            if ln.strip().startswith("**"):
                # **Simpaisa (B2B Fintech Platform)** | Dubai, UAE | 5 Markets (PK, BD, NP, EG, IQ) | Aug 2020 – Present
                meta = ln.strip()
                meta_parts = [p.strip().strip("*") for p in meta.split("|")]
                if meta_parts:
                    role["company"] = meta_parts[0]
                    if len(meta_parts) >= 2:
                        role["location"] = meta_parts[1]
                    if len(meta_parts) >= 3:
                        # last part is usually dates
                        role["dates"] = meta_parts[-1]
                        if len(meta_parts) > 3:
                            role["scope"] = " | ".join(meta_parts[2:-1])
                rest = ch_lines[j + 1:]
                break
        else:
            rest = ch_lines[1:]

        # Now rest contains optional summary line + grouped highlights
        # Pattern: "Some intro paragraph" then "**Group Header:**" then bullets
        groups = []
        current_group = None
        current_bullets = []
        intro_lines = []
        for ln in rest:
            ln_s = ln.strip()
            if not ln_s:
                continue
            if ln_s.startswith("**") and ln_s.endswith(":**"):
                # New group header
                if current_group:
                    groups.append({"label": current_group, "bullets": current_bullets})
                current_group = ln_s.strip("*").rstrip(":").strip()
                current_bullets = []
            elif ln_s.startswith("- "):
                if current_group:
                    current_bullets.append(ln_s.lstrip("- ").strip())
                else:
                    # bullets without group → flat highlights
                    role["highlights"].append(ln_s.lstrip("- ").strip())
            else:
                if not current_group and not role["highlights"]:
                    intro_lines.append(ln_s)
        if current_group:
            groups.append({"label": current_group, "bullets": current_bullets})
        role["summary"] = " ".join(intro_lines)
        role["groups"] = groups
        profile["experience"].append(role)

    # Education
    edu_raw = sections.get("Education", "")
    for block in re.split(r"\n\n+", edu_raw):
        block = block.strip()
        if not block:
            continue
        # First **Bold** line: degree | school | year
        m = re.match(r"\*\*([^*]+)\*\*", block)
        if m:
            top = m.group(1).strip()
            # split on |
            parts = [p.strip() for p in top.split("|")]
            entry = {"raw": top, "year": ""}
            if len(parts) >= 2:
                entry["title"] = parts[0]
                entry["year"] = parts[-1] if re.search(r"\d{4}", parts[-1]) else ""
                entry["details"] = " | ".join(parts[1:-1]) if entry["year"] else " | ".join(parts[1:])
            else:
                entry["title"] = top
            # Following lines: notes
            tail = block.split("\n", 1)
            if len(tail) > 1:
                entry["notes"] = tail[1].strip()
            profile["education"].append(entry)

    # Certifications
    cert_raw = sections.get("Certifications", "")
    for ln in cert_raw.splitlines():
        ln = ln.strip()
        if ln.startswith("- "):
            ln = ln.lstrip("- ").strip()
            # **PMP** (Project Management Professional)
            m = re.match(r"\*\*([^*]+)\*\*\s*(?:\(([^)]+)\))?", ln)
            if m:
                profile["certifications"].append({
                    "name": m.group(1).strip(),
                    "full_name": m.group(2).strip() if m.group(2) else "",
                })

    # Technical Knowledge — pipe-separated
    tk_raw = sections.get("Technical Knowledge", "").strip()
    profile["technical_knowledge"] = [s.strip() for s in tk_raw.split("|") if s.strip()]

    # Languages
    lang_raw = sections.get("Languages", "")
    for ln in lang_raw.splitlines():
        ln = ln.strip()
        if ln.startswith("- "):
            ln = ln.lstrip("- ").strip()
            m = re.match(r"\*\*([^*]+)\*\*\s*[—-]\s*(.+)", ln)
            if m:
                profile["languages"].append({"name": m.group(1).strip(), "level": m.group(2).strip()})

    # Tailored resume count
    tail_raw = sections.get("Resumes Tailored for Specific Roles", "")
    nums = re.findall(r"^\s*\d+\.\s+", tail_raw, flags=re.MULTILINE)
    profile["tailored_resumes_count"] = len(nums)

    return profile


def main():
    profile = parse()
    out = OUT / "master_profile.json"
    out.write_text(json.dumps(profile, indent=2))
    print(f"✅ Wrote {out}")
    print(f"   Name: {profile['name']}")
    print(f"   Summary: {len(profile['summary'])} chars")
    print(f"   Core competencies: {len(profile['core_competencies'])}")
    print(f"   AI solutions: {len(profile['ai_solutions'])}")
    print(f"   Experience entries: {len(profile['experience'])}")
    print(f"   Education: {len(profile['education'])}")
    print(f"   Certifications: {len(profile['certifications'])}")
    print(f"   Technical knowledge items: {len(profile['technical_knowledge'])}")
    print(f"   Languages: {len(profile['languages'])}")
    for r in profile["experience"]:
        print(f"   - {r['title']} @ {r['company'][:40]} | {r['dates']} | groups={len(r.get('groups',[]))}")


if __name__ == "__main__":
    main()
