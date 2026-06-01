"""Regression tests for JobScoutAgent._url_matches_company (mislabel guard).

This validator drops Perplexity-discovered candidates whose URL doesn't
plausibly belong to the target company — the fix for production mislabels
like query="STC Pay" returning a pnc.wd5.myworkdayjobs.com (PNC Bank) URL.

Because a false POSITIVE silently labels another company's job as a target
(and a false NEGATIVE silently drops a legit job), this is unit-tested
exhaustively. Cases include the original production bugs, the real open rows
found live in the DB on 2026-05-29, and the short-slug substring edge cases
that the 2026-05-29 token-boundary hardening closed.

The method only depends on `self._company_slug` + `self._KNOWN_ATS_HOSTS`, so
we bind it onto a tiny shim instead of constructing the full agent (which
needs DB/env).
"""
import os
import sys

import pytest

# Make the repo root importable when this file is run on its own
# (`pytest tests/test_scout_url_match.py`); the full-suite run already has it.
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agents.job_scout_agent import JobScoutAgent


class _Shim:
    """Minimal carrier so we can call the instance method without __init__."""
    _KNOWN_ATS_HOSTS = JobScoutAgent._KNOWN_ATS_HOSTS
    _company_slug = JobScoutAgent._company_slug
    _url_matches_company = JobScoutAgent._url_matches_company


_match = _Shim()._url_matches_company  # (url, company, domain=None, careers_url=None)


# (label, url, company, domain, careers_url)
_ACCEPT = [
    # ── distinctive single-token slugs (host first-label) ──────────────
    ("mastercard workday",
     "https://mastercard.wd1.myworkdayjobs.com/CorporateCareers/job/PM/1", "Mastercard", None, None),
    ("paypal workday",
     "https://paypal.wd1.myworkdayjobs.com/jobs/job/55", "PayPal", None, None),
    ("visa workday host",
     "https://visa.wd5.myworkdayjobs.com/Jobs/job/Senior-PM/99", "Visa", None, None),
    ("careers.adyen.com bare host",
     "https://careers.adyen.com/job/123", "Adyen", None, None),
    # ── hand-coded abbreviations ───────────────────────────────────────
    ("amex via abbrev",
     "https://amex.wd1.myworkdayjobs.com/Jobs/job/77", "American Express", None, None),
    ("jpmc via abbrev",
     "https://jpmc.wd5.myworkdayjobs.com/jpmc/job/88", "JPMorgan Chase", None, None),
    ("scb via abbrev",
     "https://scb.wd3.myworkdayjobs.com/scb/job/12", "Standard Chartered", None, None),
    ("enbd via abbrev",
     "https://enbd.wd3.myworkdayjobs.com/jobs/job/9", "Emirates NBD", None, None),
    # ── generic-ATS path slug (token-aligned) ──────────────────────────
    ("greenhouse path slug okx",
     "https://boards.greenhouse.io/okx/jobs/4567", "OKX", None, None),
    ("lever path slug revolut",
     "https://jobs.lever.co/revolut/abc-123", "Revolut", None, None),
    # ── brand-disjoint Workday tenants rescued by careers_url (Rule 2) ──
    ("amexgbt travelhrportal WITH careers_url",
     "https://travelhrportal.wd1.myworkdayjobs.com/Jobs/job/33",
     "American Express GBT (Global Business Travel)", None,
     "https://travelhrportal.wd1.myworkdayjobs.com/Jobs"),
    ("scb peopleplus WITH careers_url",
     "https://peopleplus.wd3.myworkdayjobs.com/job/1", "Standard Chartered", None,
     "https://peopleplus.wd3.myworkdayjobs.com/Careers"),
    # ── live open rows confirmed legit (DB pull 2026-05-29) ────────────
    ("live checkout.com ashby",
     "https://jobs.ashbyhq.com/checkout.com/86b7dc01", "Checkout.com", None,
     "https://www.checkout.com/careers"),
    ("live finastra workday",
     "https://finastra.wd3.myworkdayjobs.com/en-US/FINC/job/Senior-PM_REQ1", "Finastra", None,
     "https://www.finastra.com/careers"),
    ("live nium lever landing",
     "https://jobs.lever.co/nium", "Nium", None, "https://www.nium.com/careers"),
    ("live temenos workday",
     "https://temenos.wd103.myworkdayjobs.com/en-US/Temenoscareers/job/Owner", "Temenos", None,
     "https://www.temenos.com/about-us/careers/"),
    ("live visa smartrecruiters",
     "https://jobs.smartrecruiters.com/Visa/744000116287926-sr-product-manager-", "Visa", None,
     "https://corporate.visa.com/en/careers.html"),
    ("live wise smartrecruiters",
     "https://jobs.smartrecruiters.com/Wise/744000115694423-senior-pm", "Wise", None,
     "https://wise.jobs"),
    ("live worldline lever",
     "https://jobs.lever.co/Worldline/0dea2ae5", "Worldline", None,
     "https://worldline.com/en/home/careers.html"),
]

# (label, url, company, domain, careers_url)
_REJECT = [
    # ── original production mislabels ──────────────────────────────────
    ("PROD: STC Pay <- PNC workday",
     "https://pnc.wd5.myworkdayjobs.com/PNC_External_Careers/job/123", "STC Pay", None, None),
    ("PROD: MagnatiPay <- Worldpay workday",
     "https://worldpay.wd5.myworkdayjobs.com/Worldpay/job/abc", "MagnatiPay", None, None),
    ("cross-company: Adyen <- Stripe",
     "https://stripe.com/jobs/listing/pm/55", "Adyen", None, None),
    ("cross-company: Mastercard <- Citi workday",
     "https://citi.wd5.myworkdayjobs.com/Citi/job/x", "Mastercard", None, None),
    # ── live residual mislabels found in DB (2026-05-29) ───────────────
    ("live: 2Checkout <- workday maintenance page",
     "https://community.workday.com/maintenance-page?d=3", "2Checkout", None,
     "https://www.2checkout.com/careers"),
    ("live: 2Checkout <- Checkout.com ashby",
     "https://jobs.ashbyhq.com/checkout.com/b0512116", "2Checkout", None,
     "https://www.2checkout.com/careers"),
    ("live: Currencycloud <- Green Dot workday",
     "https://greendotcorp.wd1.myworkdayjobs.com/en-US/GDC/job/PM_R3752", "Currencycloud", None,
     "https://www.currencycloud.com/careers/"),
    ("live: Currencycloud <- Zillow workday",
     "https://zillow.wd5.myworkdayjobs.com/en-US/Zillow_Group_External/job/PM_P749079",
     "Currencycloud", None, "https://www.currencycloud.com/careers/"),
    ("live: STC Pay <- SWBC/Swivel workday",
     "https://swbc.wd1.myworkdayjobs.com/en-US/Swivel/job/Senior-Payments-PM_R0014196",
     "STC Pay", None, "https://careers.stcpay.com.sa"),
    # ── short-slug substring FP, closed by token-boundary hardening ────
    ("FP: short slug 'stc' inside 'westcoast' path",
     "https://boards.greenhouse.io/westcoast-labs/jobs/123", "STC", None, None),
    ("FP: slug 'unit' inside 'opportunity' path",
     "https://boards.greenhouse.io/acme/jobs/opportunity-pm", "Unit", None, None),
    # ── null / malformed inputs ────────────────────────────────────────
    ("edge: empty url", "", "Mastercard", None, None),
    ("edge: empty company", "https://mastercard.wd1.myworkdayjobs.com/x", "", None, None),
    ("edge: garbage url no host", "notaurl", "Mastercard", None, None),
]


@pytest.mark.parametrize("label,url,company,domain,careers",
                         _ACCEPT, ids=[c[0] for c in _ACCEPT])
def test_url_matches_company_accepts_legit(label, url, company, domain, careers):
    assert _match(url, company, domain, careers) is True, label


@pytest.mark.parametrize("label,url,company,domain,careers",
                         _REJECT, ids=[c[0] for c in _REJECT])
def test_url_matches_company_rejects_mislabels(label, url, company, domain, careers):
    assert _match(url, company, domain, careers) is False, label
