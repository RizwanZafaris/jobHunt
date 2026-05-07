"""
SalaryResearchAgent — Compensation benchmarking and negotiation intelligence.

Researches market rates for target roles in Gulf + UK markets.
Generates negotiation scripts and offer evaluation frameworks.
"""

from __future__ import annotations
import logging
from typing import Optional
from datetime import date

from agents.base_agent import BaseAgent
from config.settings import get_settings

logger = logging.getLogger(__name__)


# Rizwan's compensation profile
RIZWAN_COMP_PROFILE = {
    "target_monthly_aed": 50000,    # AED 50K/month ideal
    "minimum_monthly_aed": 35000,   # AED 35K/month walk-away
    "equity_interest": True,        # Open to equity packages
    "bonus_interest": True,         # Performance bonus is additive
    "relocation_allowance": True,   # Needed for KSA/Qatar/UK roles
    "notice_period_months": 1,      # Can start in 1 month
}

# Market benchmarks (updated from public sources — may need refreshing)
MARKET_BENCHMARKS = {
    "UAE": {
        "Head of Product": {"min": 35000, "median": 50000, "max": 70000, "currency": "AED"},
        "Senior Product Manager": {"min": 25000, "median": 35000, "max": 50000, "currency": "AED"},
        "CPO": {"min": 50000, "median": 70000, "max": 120000, "currency": "AED"},
        "Technical Programme Manager": {"min": 30000, "median": 45000, "max": 65000, "currency": "AED"},
        "VP Product": {"min": 45000, "median": 65000, "max": 100000, "currency": "AED"},
    },
    "KSA": {
        "Head of Product": {"min": 30000, "median": 45000, "max": 65000, "currency": "SAR"},
        "Senior Product Manager": {"min": 20000, "median": 30000, "max": 45000, "currency": "SAR"},
        "CPO": {"min": 45000, "median": 65000, "max": 110000, "currency": "SAR"},
    },
    "UK": {
        "Head of Product": {"min": 90000, "median": 120000, "max": 160000, "currency": "GBP"},
        "Senior Product Manager": {"min": 70000, "median": 95000, "max": 130000, "currency": "GBP"},
        "CPO": {"min": 130000, "median": 180000, "max": 250000, "currency": "GBP"},
    },
}

# AED → other currency rough conversions (update as needed)
CONVERSION_RATES = {
    "AED": 1.0,
    "SAR": 1.02,    # SAR roughly = AED
    "GBP": 0.22,    # 1 AED ≈ 0.22 GBP (approximate)
    "USD": 0.27,    # 1 AED ≈ 0.27 USD
}


class SalaryResearchAgent(BaseAgent):
    """
    Researches compensation and generates negotiation intelligence.

    Capabilities:
    - Market rate benchmarking by role + location
    - Offer evaluation (is this offer fair?)
    - Negotiation script generation
    - Total compensation analysis (base + bonus + equity + benefits)
    - Counter-offer strategy
    """

    def __init__(self):
        settings = get_settings()
        super().__init__(name="SalaryResearch", model=settings.rizwan_agent_model)

    async def research_compensation(
        self,
        company: str,
        job_title: str,
        location: str,
        company_stage: Optional[str] = None,  # "startup", "scaleup", "enterprise"
    ) -> dict:
        """
        Research market compensation for a role.
        Returns benchmarks, positioning, and negotiation guidance.
        """
        self.log(f"Researching compensation: {job_title} @ {company} ({location})")

        # Get benchmark data
        market_data = self._get_market_data(job_title, location)

        # Research specific company compensation
        company_comp = await self._research_company_comp(company, job_title, location)

        # Generate strategy
        strategy = await self._generate_negotiation_strategy(
            company=company,
            job_title=job_title,
            location=location,
            market_data=market_data,
            company_stage=company_stage,
        )

        return {
            "company": company,
            "role": job_title,
            "location": location,
            "market_benchmarks": market_data,
            "company_specific_intel": company_comp,
            "rizwan_target": RIZWAN_COMP_PROFILE["target_monthly_aed"],
            "rizwan_minimum": RIZWAN_COMP_PROFILE["minimum_monthly_aed"],
            "negotiation_strategy": strategy,
            "research_date": date.today().isoformat(),
        }

    def _get_market_data(self, job_title: str, location: str) -> dict:
        """Look up market benchmarks from local data."""
        # Normalize location to country
        country_map = {
            "dubai": "UAE", "abu dhabi": "UAE", "uae": "UAE",
            "riyadh": "KSA", "jeddah": "KSA", "ksa": "KSA", "saudi": "KSA",
            "doha": "Qatar", "qatar": "Qatar",
            "london": "UK", "uk": "UK", "england": "UK",
        }
        location_lower = location.lower()
        country = "UAE"  # Default
        for key, val in country_map.items():
            if key in location_lower:
                country = val
                break

        # Normalize role to benchmark category
        role_map = {
            "head of product": "Head of Product",
            "chief product officer": "CPO",
            "cpo": "CPO",
            "vp product": "VP Product",
            "senior product manager": "Senior Product Manager",
            "technical programme manager": "Technical Programme Manager",
            "technical program manager": "Technical Programme Manager",
        }
        role_key = None
        job_lower = job_title.lower()
        for key, val in role_map.items():
            if key in job_lower:
                role_key = val
                break
        if not role_key:
            role_key = "Head of Product"  # Closest default for Rizwan's level

        benchmarks = MARKET_BENCHMARKS.get(country, MARKET_BENCHMARKS["UAE"])
        data = benchmarks.get(role_key, benchmarks.get("Head of Product", {}))

        # Convert to AED for comparison
        currency = data.get("currency", "AED")
        rate = CONVERSION_RATES.get(currency, 1.0)
        aed_factor = 1.0 / rate if rate > 0 else 1.0

        return {
            "country": country,
            "role_category": role_key,
            "local_currency": currency,
            "local_min": data.get("min"),
            "local_median": data.get("median"),
            "local_max": data.get("max"),
            "aed_equivalent_min": round(data.get("min", 0) * aed_factor),
            "aed_equivalent_median": round(data.get("median", 0) * aed_factor),
            "aed_equivalent_max": round(data.get("max", 0) * aed_factor),
            "rizwan_target_vs_market": self._position_vs_market(
                RIZWAN_COMP_PROFILE["target_monthly_aed"],
                round(data.get("median", 0) * aed_factor),
            ),
        }

    def _position_vs_market(self, target_aed: int, market_median_aed: int) -> str:
        if market_median_aed == 0:
            return "unknown"
        ratio = target_aed / market_median_aed
        if ratio <= 0.85:
            return "below_market"
        elif ratio <= 1.10:
            return "at_market"
        elif ratio <= 1.30:
            return "above_market"
        else:
            return "significantly_above_market"

    async def _research_company_comp(
        self, company: str, job_title: str, location: str
    ) -> dict:
        """Research company-specific compensation from public sources."""
        system = "You are a compensation researcher with deep knowledge of Gulf and UK tech markets."
        user = f"""Research typical compensation for {job_title} at {company} in {location}.

Sources to consider:
- Glassdoor salary data for {company}
- LinkedIn salary insights
- Levels.fyi (if tech company)
- Public job postings that mention salary ranges
- Company stage and funding (affects comp significantly)
- Country-specific tax treatment (UAE = 0% income tax)

Provide:
1. Estimated base salary range (monthly, local currency + AED equivalent)
2. Bonus structure (typical %, at-risk vs guaranteed)
3. Equity (ESOP %, vesting schedule if startup/scaleup)
4. Benefits (housing allowance, health insurance, flights home)
5. Data confidence: HIGH / MEDIUM / LOW (based on data availability)
6. Key comp leverage points for negotiation

Be specific. If you don't have data for this company, use closest comparable."""

        try:
            response = await self.ask_claude(
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=800,
                temperature=0.3,
            )
            return {"raw_intel": response}
        except Exception as e:
            logger.warning(f"Company comp research failed: {e}")
            return {"raw_intel": "Data unavailable — use market benchmarks."}

    async def _generate_negotiation_strategy(
        self,
        company: str,
        job_title: str,
        location: str,
        market_data: dict,
        company_stage: Optional[str],
    ) -> str:
        """Generate a step-by-step negotiation strategy."""
        system = "You are a senior career coach specializing in tech executive compensation negotiation."
        user = f"""Generate a negotiation strategy for Rizwan Zafar negotiating {job_title} at {company} in {location}.

Rizwan's profile:
- 14 years fintech/payments experience
- Currently CPO at SimPaisa ($1B+ TPV, 40+ engineers)
- Target: AED {RIZWAN_COMP_PROFILE['target_monthly_aed']:,}/month
- Minimum: AED {RIZWAN_COMP_PROFILE['minimum_monthly_aed']:,}/month
- UAE tax-free advantage (vs UK roles)

Market data: {market_data}
Company stage: {company_stage or 'unknown'}

Produce a negotiation strategy covering:
1. Opening anchor (where to start the conversation)
2. Justification talking points (why this level is fair)
3. Trading chips (what to give up to get base comp up)
4. Non-monetary asks (equity, signing bonus, extra leave, remote days)
5. Walk-away criteria
6. Exact script for the salary conversation opener
7. How to respond to "What are you currently making?" (Dubai context: no payslip required)

Be tactical and specific. Use Rizwan's actual experience as leverage points."""

        return await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=1000,
            temperature=0.3,
        )

    async def evaluate_offer(
        self,
        company: str,
        job_title: str,
        location: str,
        offered_base_monthly: float,
        offered_currency: str = "AED",
        bonus_pct: float = 0,
        equity_details: str = "",
        other_benefits: str = "",
    ) -> dict:
        """Evaluate a job offer against market benchmarks and Rizwan's minimums."""
        market_data = self._get_market_data(job_title, location)

        # Normalize to AED
        rate = CONVERSION_RATES.get(offered_currency, 1.0)
        aed_factor = 1.0 / rate
        offered_aed = offered_base_monthly * aed_factor

        # Annualized figures
        annual_base_aed = offered_aed * 12
        annual_bonus_aed = annual_base_aed * (bonus_pct / 100)

        # Assessment
        vs_minimum = offered_aed >= RIZWAN_COMP_PROFILE["minimum_monthly_aed"]
        vs_target = offered_aed >= RIZWAN_COMP_PROFILE["target_monthly_aed"]
        vs_market = self._position_vs_market(offered_aed, market_data.get("aed_equivalent_median", 50000))

        system = "You are a career advisor evaluating a job offer."
        user = f"""Evaluate this offer for Rizwan and advise on acceptance/negotiation:

Company: {company}
Role: {job_title}
Location: {location}
Offered base: {offered_currency} {offered_base_monthly:,.0f}/month (= AED {offered_aed:,.0f}/month)
Bonus: {bonus_pct}% of base
Equity: {equity_details or 'None mentioned'}
Other benefits: {other_benefits or 'Standard'}

Market median (AED): {market_data.get('aed_equivalent_median', 'N/A')}
Rizwan's target (AED): {RIZWAN_COMP_PROFILE['target_monthly_aed']:,}
Meets minimum: {'YES' if vs_minimum else 'NO — BELOW WALK-AWAY'}
Vs market: {vs_market}

Provide:
1. Offer verdict: ACCEPT / NEGOTIATE / REJECT
2. If negotiate: specific counter-offer numbers with justification
3. What to say when counter-offering
4. Red flags in the offer structure (if any)
5. Total comp analysis (base + bonus + equity value + benefits)"""

        recommendation = await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=800,
            temperature=0.3,
        )

        return {
            "company": company,
            "role": job_title,
            "offered_monthly_aed": round(offered_aed),
            "vs_minimum": "MEETS" if vs_minimum else "BELOW — REJECT",
            "vs_target": "MEETS" if vs_target else "BELOW TARGET",
            "vs_market": vs_market,
            "annual_base_aed": round(annual_base_aed),
            "annual_total_aed": round(annual_base_aed + annual_bonus_aed),
            "recommendation": recommendation,
        }
