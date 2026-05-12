"""
DocumentClassifier — assigns a `document_class` to each source document in the
`profile_source_document` table.

BUG-016 (2026-05-12)
====================
Historic flow (`profile_build/01_inventory.py::classify`) treated almost
anything under `/Resume/` with `.pdf`/`.docx` extension and any of a wide
"role keyword" list as a `role_specific_resume`. That swept up 174 rows,
many of which are NOT resumes:

  * `Cover letter.pdf`                       → real class: `cover_letter`
  * `Chief Product Officer (CPO) JD.pdf`     → real class: `job_description`
  * `VP of Product - Qlub (Dubai).pdf`       → real class: `job_description`
  * `Roadmap Q3-Q4-2024 - Portal.pdf`        → real class: `roadmap`
  * `Roadmap Q3-Q4-2024 - PayIns.pdf`        → real class: `roadmap`
  * `Roadmap Q3-Q4-2024 - PayOuts.pdf`       → real class: `roadmap`
  * `mastercard_hr_question_bank.docx`       → real class: `interview_prep`
  * `Doc2.docx` (1.4KB junk)                 → real class: `misc_short`
  * 707-char garbage OCR fragments           → real class: `misc_short`

These rows then fed `profile_keyword` (26 756 occurrences total) which
inflated ATS scoring and biased G2 prompts toward irrelevant vocabulary.

New canonical `document_class` values introduced here
-----------------------------------------------------
  * `job_description`  — Job Description / Role spec PDFs and docs.
  * `cover_letter`     — already existed; now also detected from generic
                         filenames like "Cover letter.pdf".
  * `roadmap`          — Product roadmap docs ("Roadmap QN YYYY", "Q3 2024").
  * `misc_short`       — Junk: file_size < 2 000 B AND char_count < 500.

Existing values left untouched: `master_cv`, `executive_resume`,
`role_specific_resume`, `general_resume`, `linkedin_*`, `application_answer`,
`interview_prep`, `company_assessment`, `recruiter_analysis`, `target_roles`,
`job_search_report`, `simpaisa_*`, `rizwan_markdown`, `other`.

Public API
----------
  classify_document(file_name, file_path=None, file_size=None,
                    char_count=None, extracted_text=None) -> str
       The single entry point. Pure function, no I/O. Pass whatever you
       have; richer inputs → stronger classifications.

The function is intentionally heuristic only (no LLM call) so it is fast,
deterministic, idempotent, and free. Reclassify-time text extraction is
also possible by passing `extracted_text` if you've already pulled the
content out of a PDF/DOCX.
"""

from __future__ import annotations

import re
from typing import Optional

# ── Sentinel constants ────────────────────────────────────────────────────
JUNK_FILE_SIZE = 2_000      # bytes
JUNK_CHAR_COUNT = 500       # extracted chars

# ── Pre-compiled patterns ─────────────────────────────────────────────────

_COVER_LETTER_NAME_RX = re.compile(
    r"\bcover[ _\-]?letter\b", re.IGNORECASE
)
# A cover letter usually opens with one of these salutations
_COVER_LETTER_OPENER_RX = re.compile(
    r"^\s*[•\-*]*\s*(?:Dear|Hi|Hello)\s+[A-Z]", re.IGNORECASE | re.MULTILINE
)
_COVER_LETTER_BODY_RX = re.compile(
    r"\b("
    r"i am writing to apply"
    r"|i am excited to apply"
    r"|i am writing to express"
    r"|i would like to apply"
    r"|writing to express my interest"
    r"|your team"
    r"|your company"
    r"|your organi[sz]ation"
    r")\b",
    re.IGNORECASE,
)

# Filename signals that strongly suggest a JD
_JD_NAME_RX = re.compile(
    r"("
    r"\bJD\b"
    r"|job[ _\-]?description"
    r"|role[ _\-]?spec"
    r"|\b(?:CPO|CTO|CFO|CHRO|VP|SVP|EVP|Head|Director|Chief)\b.*\b(?:JD|Role|Position)\b"
    r"|\bVP of\b|\bHead of\b"
    r")",
    re.IGNORECASE,
)
_JD_BODY_HEADERS_RX = re.compile(
    r"\b("
    r"job description"
    r"|responsibilities"
    r"|qualifications"
    r"|what you[''']ll do"
    r"|about the role"
    r"|about this role"
    r"|we are looking for"
    r"|key requirements"
    r"|role overview"
    r"|the role"
    r")\b",
    re.IGNORECASE,
)

# Roadmap signals — explicit "Roadmap" in name OR Q-suffix year stamps in body
_ROADMAP_NAME_RX = re.compile(r"\broadmap\b", re.IGNORECASE)
_ROADMAP_BODY_RX = re.compile(
    r"\bQ[1-4][\- ]?202[0-9]\b|\bQ[1-4][\- ]?Q[1-4][\- ]?202[0-9]\b",
    re.IGNORECASE,
)

# Interview-prep filename signals beyond the original heuristic
_INTERVIEW_PREP_RX = re.compile(
    r"(question_bank|qa_bank|interview_prep|interview_master|complete_prep)",
    re.IGNORECASE,
)


def _is_cover_letter(
    name_l: str, text_l: Optional[str]
) -> bool:
    if _COVER_LETTER_NAME_RX.search(name_l):
        return True
    if not text_l:
        return False
    if _COVER_LETTER_OPENER_RX.search(text_l):
        return True
    if _COVER_LETTER_BODY_RX.search(text_l):
        # require at least 2 cover-letter phrases to avoid false positives
        # in generic resumes that happen to mention "your team" once
        hits = len(_COVER_LETTER_BODY_RX.findall(text_l))
        return hits >= 2
    return False


def _is_job_description(name_l: str, text_l: Optional[str]) -> bool:
    if _JD_NAME_RX.search(name_l):
        return True
    if not text_l:
        return False
    # need at least 2 distinct JD headers to call it a JD from body alone
    hits = set()
    for m in _JD_BODY_HEADERS_RX.findall(text_l):
        hits.add(m.lower() if isinstance(m, str) else str(m).lower())
    return len(hits) >= 2


def _is_roadmap(name_l: str, text_l: Optional[str]) -> bool:
    if _ROADMAP_NAME_RX.search(name_l):
        return True
    if not text_l:
        return False
    return bool(_ROADMAP_BODY_RX.search(text_l))


def _is_misc_short(file_size: Optional[int], char_count: Optional[int]) -> bool:
    """Junk: very small file AND very little extracted text."""
    if file_size is None or char_count is None:
        return False
    return file_size < JUNK_FILE_SIZE and char_count < JUNK_CHAR_COUNT


def classify_document(
    file_name: str,
    file_path: Optional[str] = None,
    file_size: Optional[int] = None,
    char_count: Optional[int] = None,
    extracted_text: Optional[str] = None,
) -> Optional[str]:
    """Return the canonical document_class for a single file, or None if no
    rule fired (caller decides whether to fall back to the prior heuristic
    in `profile_build/01_inventory.py::classify`).

    Order matters. JD wins over cover letter (a JD can quote "Dear hiring
    manager"); junk wins last (we still want to label, e.g., a 1-page real
    cover letter correctly).
    """
    name_l = (file_name or "").lower()
    path_l = (file_path or "").lower()
    # The path can carry strong signals (e.g. `/Network/Cover letter.pdf`
    # lives next to `Hiring Manager.docx` — both cover letters).
    combined_name = (name_l + " " + path_l).strip()
    text_l = extracted_text.lower() if extracted_text else None

    # ── 1. JD ────────────────────────────────────────────────────────────
    if _is_job_description(combined_name, text_l):
        return "job_description"

    # ── 2. cover letter ──────────────────────────────────────────────────
    if _is_cover_letter(combined_name, text_l):
        return "cover_letter"

    # ── 3. roadmap ───────────────────────────────────────────────────────
    if _is_roadmap(combined_name, text_l):
        return "roadmap"

    # ── 4. interview prep (these were misclassified too) ─────────────────
    if _INTERVIEW_PREP_RX.search(combined_name):
        return "interview_prep"
    # Path-based: anything in an Interview_Prep folder
    if "/interview_prep/" in path_l or "interview_prep" in path_l:
        return "interview_prep"

    # ── 5. junk ──────────────────────────────────────────────────────────
    if _is_misc_short(file_size, char_count):
        return "misc_short"

    return None
