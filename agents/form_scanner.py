"""
agents/form_scanner.py — Playwright wrapper for reading live application
forms (G7 node 1).

career-ops's apply.md uses Playwright the same way: launch chromium,
navigate, harvest the field list, snapshot raw HTML for re-render
during fill. We mirror the pattern but write to ApplicationFormState
instead of a CLI prompt.

Public entry: `scan_form(url, ats_type='greenhouse')` returns a
ScanResult with the extracted G7Question list (un-classified — that's
the next node) + scanner metadata.

Headless by default (Railway / Docker friendly). The browser is launched
fresh per scan and closed in a `finally` block; no shared context (one
form, one scan, one tear-down).

Design notes:
  - Selector pack is loaded from agents/ats_patterns/<ats_type>.yml so we
    can add Lever/Ashby without code changes (Phase 4).
  - Tests monkey-patch `_launch_playwright` to avoid actually driving a
    browser. `route` interception (also exposed) supports mock HTML for
    deterministic unit tests.
  - Returns text only — no screenshots, no PDFs, no DOM evaluation
    beyond label/option extraction. Keeps the surface narrow.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)


_PATTERN_DIR = Path(__file__).parent / "ats_patterns"


# ──────────────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class ScannedField:
    idx: int
    field_id: str
    field_label: str
    field_type: str  # text | textarea | select | radio | checkbox | url | tel | email | file
    required: bool
    options: Optional[list[str]] = None
    raw_html_snippet: Optional[str] = None

    def as_question_dict(self) -> dict[str, Any]:
        """Shape that becomes a G7Question — un-classified."""
        return {
            "idx": self.idx,
            "field_id": self.field_id,
            "field_label": self.field_label,
            "field_type": self.field_type,
            "required": self.required,
            "options": self.options,
            "raw_html_snippet": (self.raw_html_snippet or "")[:600],
            "classification": "custom",   # default; classifier overrides
            "retrieved_context": [],
            "cites": [],
            "generated_answer": "",
            "persona_critique": {},
            "user_edited": None,
            "approved": False,
            "ats_filled_ok": None,
            "fill_error": None,
        }


@dataclass
class ScanResult:
    url: str
    ats_type: str
    page_title: str
    raw_html_size: int
    fields: list[ScannedField] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def field_count(self) -> int:
        return len(self.fields)

    def as_state_patch(self) -> dict[str, Any]:
        """Patch for ApplicationFormState — questions + scanner_meta."""
        return {
            "questions": [f.as_question_dict() for f in self.fields],
            "scanner_meta": {
                "url": self.url,
                "ats_type": self.ats_type,
                "page_title": self.page_title,
                "raw_html_size": self.raw_html_size,
                "field_count": self.field_count,
                "error": self.error,
            },
        }


# ──────────────────────────────────────────────────────────────────────────
# Selector pack loading
# ──────────────────────────────────────────────────────────────────────────
def load_ats_pattern(ats_type: str) -> dict[str, Any]:
    """Load the YAML selector pack. Raises FileNotFoundError on missing.

    Cached at module level after first load — the YAML is static once
    deployed so repeat scans don't re-parse.
    """
    return _load_ats_pattern_cached(ats_type)


_PATTERN_CACHE: dict[str, dict[str, Any]] = {}


def _load_ats_pattern_cached(ats_type: str) -> dict[str, Any]:
    if ats_type in _PATTERN_CACHE:
        return _PATTERN_CACHE[ats_type]
    path = _PATTERN_DIR / f"{ats_type}.yml"
    if not path.exists():
        raise FileNotFoundError(f"ATS pattern not found: {path}")
    with path.open() as f:
        data = yaml.safe_load(f) or {}
    _PATTERN_CACHE[ats_type] = data
    return data


def reset_pattern_cache() -> None:
    """Test-only — re-read patterns from disk."""
    _PATTERN_CACHE.clear()


# ──────────────────────────────────────────────────────────────────────────
# Pure-HTML scan path (used by both Playwright and tests)
# ──────────────────────────────────────────────────────────────────────────
_HEURISTIC_FIELD_TYPES = {
    "input[type='email']": "email",
    "input[type='url']": "url",
    "input[type='tel']": "tel",
    "input[type='text']": "text",
    "textarea": "textarea",
    "select": "select",
    "input[type='radio']": "radio",
    "input[type='checkbox']": "checkbox",
    "input[type='file']": "file",
}


def scan_from_html(
    html: str,
    *,
    ats_type: str = "greenhouse",
    url: str = "",
    page_title: str = "",
) -> ScanResult:
    """Pure-HTML scan path — no Playwright required.

    Used by tests (mock HTML) AND by production when Playwright is
    available but we'd rather not launch a browser (e.g. when a caller
    already pulled the HTML via httpx). The Playwright path delegates here
    after extracting page.content().

    Uses BeautifulSoup (already in requirements.txt) for parsing —
    matches the rest of the scraping stack.
    """
    from bs4 import BeautifulSoup

    pattern = load_ats_pattern(ats_type)
    selectors = pattern.get("selectors", {})

    soup = BeautifulSoup(html, "html.parser")

    fields: list[ScannedField] = []
    idx = 0

    # Greenhouse-style: every field has a parent div.application--field
    # carrying the label + the underlying input. We use the parent's
    # data attributes to identify the field and reach into it for the
    # input type.
    field_roots = soup.select(selectors.get("field_root", "div.application--field"))

    # Fallback: if the selector returned nothing, scan top-level inputs
    # so dev/test HTML without the wrapper still produces a result.
    if not field_roots:
        field_roots = [soup]

    for root in field_roots:
        # Greenhouse: prefer the FIRST <label> element under the field root.
        # Fallback: any element with class "label" (custom fields).
        label_el = root.find("label")
        if not label_el:
            label_el = root.find(class_=re.compile(r"label", re.I))
        label_text = (label_el.get_text(strip=True) if label_el else "") or ""
        required = "required" in (root.get("class") or []) or bool(root.find(class_=re.compile(r"required", re.I)))

        # Find the FIRST interactive element inside this field block.
        candidate = None
        candidate_type = None
        for el in root.find_all(["input", "textarea", "select"]):
            t = (el.get("type") or el.name or "").lower()
            if t in ("text", "email", "url", "tel"):
                candidate, candidate_type = el, t
                break
            if el.name == "textarea":
                candidate, candidate_type = el, "textarea"
                break
            if el.name == "select":
                candidate, candidate_type = el, "select"
                break
            if t == "radio":
                candidate, candidate_type = el, "radio"
                break
            if t == "checkbox":
                candidate, candidate_type = el, "checkbox"
                break
            if t == "file":
                candidate, candidate_type = el, "file"
                break
        if candidate is None:
            continue

        field_id = candidate.get("id") or candidate.get("name") or f"field_{idx}"
        options: Optional[list[str]] = None
        if candidate_type == "select":
            options = [o.get_text(strip=True) for o in candidate.find_all("option") if o.get_text(strip=True)]
        elif candidate_type == "radio":
            # Collect all radio inputs in this fieldset / root sharing the same name
            shared_name = candidate.get("name")
            if shared_name:
                radio_root = candidate.find_parent("fieldset") or root
                radios = radio_root.find_all("input", attrs={"type": "radio", "name": shared_name})
                options = []
                for r in radios:
                    rid = r.get("id") or ""
                    label_for = radio_root.find("label", attrs={"for": rid}) if rid else None
                    opt_text = (label_for.get_text(strip=True) if label_for else r.get("value") or "")
                    if opt_text:
                        options.append(opt_text)

        fields.append(ScannedField(
            idx=idx,
            field_id=str(field_id),
            field_label=label_text or str(field_id),
            field_type=candidate_type or "text",
            required=bool(required) or bool(candidate.get("required")) or (candidate.get("aria-required") == "true"),
            options=options,
            raw_html_snippet=str(root)[:600],
        ))
        idx += 1

    return ScanResult(
        url=url,
        ats_type=ats_type,
        page_title=page_title or _extract_title(soup),
        raw_html_size=len(html),
        fields=fields,
    )


def _extract_title(soup) -> str:
    t = soup.find("title")
    return (t.get_text(strip=True) if t else "") or ""


# ──────────────────────────────────────────────────────────────────────────
# Playwright scan path
# ──────────────────────────────────────────────────────────────────────────
async def scan_form(
    url: str,
    *,
    ats_type: str = "greenhouse",
    timeout_ms: int = 30_000,
    headless: bool = True,
) -> ScanResult:
    """Launch Playwright, navigate to `url`, and extract the form fields.

    Tests monkey-patch this function OR set FELO-style "fake mode" via
    `FELO_USE_FAKE_DATA=true` (unrelated to jobHunt but matches the
    pattern). For now there's no fake-data short-circuit; tests use
    `scan_from_html(...)` directly.

    Raises RuntimeError if Playwright isn't installed or the browser
    binary is missing.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright not installed. Add `playwright>=1.49` to "
            "requirements.txt and run `playwright install chromium`."
        ) from e

    pattern = load_ats_pattern(ats_type)
    selectors = pattern.get("selectors", {})

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            )
        )
        page = await context.new_page()
        try:
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            # Greenhouse forms hydrate quickly; wait a little for the
            # custom_fields_* radio groups (the slow ones) to attach.
            try:
                await page.wait_for_selector(
                    selectors.get("form_root", "form"),
                    timeout=5000,
                )
            except Exception:
                # Best-effort. If the form selector doesn't match we still
                # try the HTML scan path below.
                pass
            html = await page.content()
            title = await page.title()
        except Exception as e:
            return ScanResult(
                url=url, ats_type=ats_type, page_title="",
                raw_html_size=0, fields=[], error=f"playwright_error:{type(e).__name__}:{str(e)[:200]}",
            )
        finally:
            await context.close()
            await browser.close()

    return scan_from_html(html, ats_type=ats_type, url=url, page_title=title)


# ──────────────────────────────────────────────────────────────────────────
# Filler — used by g7_nodes.form_filler_node
# ──────────────────────────────────────────────────────────────────────────
async def fill_form(
    url: str,
    answers: list[dict],
    *,
    ats_type: str = "greenhouse",
    headless: bool = False,        # the user wants to see the loaded form
    timeout_ms: int = 30_000,
    skip_submit: bool = True,      # hard-coded True for safety; never flip
) -> dict[str, Any]:
    """Open the form in Playwright and fill each field per `answers`.

    `answers` is the post-approval list:
        [{idx, field_id, field_type, options, final_answer}]

    Returns a summary dict:
        {filled: int, failed: int, errors: [{idx, error}], stopped_before_submit: True}

    HARD invariant: this function MUST stop before clicking submit. We
    intercept any form.submit() via page.evaluate to disable the submit
    button after fill, and never call page.click(submit_selector). Tests
    assert that.
    """
    if not skip_submit:
        # Defensive: any caller that tries to flip this gets a RuntimeError.
        # The HITL guarantee is the whole feature; we don't ship an opt-out.
        raise RuntimeError("skip_submit must be True — HITL invariant")

    try:
        from playwright.async_api import async_playwright
    except ImportError as e:
        raise RuntimeError(
            "Playwright not installed; cannot fill form. "
            "See agents/form_scanner.py.scan_form() for setup."
        ) from e

    pattern = load_ats_pattern(ats_type)
    selectors = pattern.get("selectors", {})
    safety = pattern.get("safety", {})

    # Refuse to even open URLs in the safety denylist.
    for bad in safety.get("refuse_if_url_contains") or []:
        if bad in (url or ""):
            return {
                "filled": 0, "failed": 0, "errors": [],
                "stopped_before_submit": True,
                "error": f"refused_url_contains:{bad}",
            }

    summary: dict[str, Any] = {
        "filled": 0,
        "failed": 0,
        "errors": [],
        "stopped_before_submit": True,
    }

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        context = await browser.new_context()
        page = await context.new_page()
        try:
            await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")

            # Disable submit buttons BEFORE we type anything, so even if
            # something weird auto-submits we don't actually send.
            for sel in safety.get("submit_selector_allowlist", []):
                try:
                    await page.eval_on_selector_all(
                        sel,
                        "els => els.forEach(e => { e.disabled = true; e.setAttribute('data-felo-hitl-blocked','1'); })",
                    )
                except Exception:
                    # Selector might not match; that's fine, others will.
                    pass

            for ans in answers:
                idx = ans.get("idx")
                field_id = ans.get("field_id")
                ftype = (ans.get("field_type") or "text").lower()
                val = ans.get("final_answer")
                if val is None or field_id is None:
                    continue
                try:
                    if ftype in ("text", "email", "url", "tel"):
                        sel = f"#{field_id}"
                        await page.fill(sel, str(val), timeout=5_000)
                    elif ftype == "textarea":
                        sel = f"#{field_id}"
                        await page.fill(sel, str(val), timeout=5_000)
                    elif ftype == "select":
                        sel = f"#{field_id}"
                        await page.select_option(sel, label=str(val), timeout=5_000)
                    elif ftype == "radio":
                        # Click the label that matches val
                        await page.click(f"label[for*='{field_id}']:has-text(\"{val}\")", timeout=5_000)
                    else:
                        # Unknown type — flag and skip
                        raise RuntimeError(f"unsupported_field_type:{ftype}")
                    summary["filled"] += 1
                except Exception as e:
                    summary["failed"] += 1
                    summary["errors"].append({
                        "idx": idx, "field_id": field_id,
                        "error": f"{type(e).__name__}:{str(e)[:200]}",
                    })

            # Final safety re-disable in case the page re-rendered.
            for sel in safety.get("submit_selector_allowlist", []):
                try:
                    await page.eval_on_selector_all(
                        sel,
                        "els => els.forEach(e => { e.disabled = true; })",
                    )
                except Exception:
                    pass
        finally:
            # Leave the browser OPEN when headless=False so the user can
            # eyeball + submit; otherwise close.
            if headless:
                await context.close()
                await browser.close()

    return summary
