"""
RizwanAgent — represents Rizwan in the dialogue with CompanyAgent.

This agent:
  1. Holds Rizwan's complete profile, experience, and stories
  2. Engages in multi-turn dialogue with CompanyAgent to fill resume gaps
  3. Provides specific evidence from Rizwan's real experience for each gap
  4. Collaborates on building the final tailored resume
  5. Generates the cover email draft
"""

from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional
import yaml
from pathlib import Path

from agents.base_agent import BaseAgent
from config.settings import get_settings
from db.client import (
    search_rizwan_profile,
    search_story_bank,
    save_conversation_turn,
    upsert_rizwan_profile,
)

logger = logging.getLogger(__name__)

# Rizwan's full experience (loaded from cv.md or profile.yml)
RIZWAN_EXPERIENCE_CONTEXT = """
RIZWAN ZAFAR — Full Experience Context for Agent

CURRENT ROLE: Technical Programme Lead / Chief Product Officer at SimPaisa (Aug 2020 – Present)
- Built Pakistan's fastest-growing digital payments platform from 0 to $1B+ TPV
- Led 40+ engineers across 12 squads (mobile, backend, payments, risk, compliance)
- Delivered Fortune 500 integrations: TikTok (in-app tipping payments), Uber (ride payments),
  MoneyGram (remittance rails), PUBG (game currency top-up)
- Owned full programme governance: RAID log across 50+ dependencies, SteerCo ownership,
  monthly board reporting
- Built regulatory-compliant payment infrastructure (SBP guidelines, 3DS, PCI-DSS)
- Launched BNPL product from 0 to 100K users in 8 months
- Ran product discovery: user research, A/B testing, OKR frameworks, feature prioritisation
- Managed MS Project plans, PowerPoint decks for C-suite and investors

PREVIOUS ROLE: Senior Product Manager at Daraz/Alibaba (2018–2020)
- Scaled Pakistan's #1 e-commerce platform (Alibaba-owned, ~100M monthly sessions)
- Led checkout, payments, and seller portal product lines
- Delivered Alipay and local payment method integrations
- Ran Agile squads of 15 engineers and 3 designers
- Reduced checkout abandonment by 22% through A/B testing and UX improvements

PREVIOUS: Programme Manager at TapmadTV (2016–2018)
- Led digital transformation of Pakistan's first licensed OTT streaming platform
- Managed $3M tech roadmap delivery
- Built PMO governance from scratch: RAID, risk registers, SteerCo

PREVIOUS: PMO Consultant (2010–2016) — worked with:
- Dubizzle (now Bayut/OLX, UAE): led digital classified platform development
- Bank Alfalah: digital banking transformation, core banking migration
- Zong (China Mobile Pakistan): network expansion programme management

KEY QUANTIFIED ACHIEVEMENTS:
- $1B+ total payment volume at SimPaisa
- 40+ engineers led across 12 squads
- 4 Fortune 500 enterprise integrations delivered
- 100K BNPL users in 8 months
- 22% checkout abandonment reduction at Daraz
- $3M programme delivered at TapmadTV
- 50+ dependency RAID log management
- 14 years total experience

CERTIFICATIONS: PMP, PMI-ACP, CSPO, CSM, COBIT 5, ITIL v3

TOOLS: MS Project, PowerPoint, Excel, Think-cell, Jira, Confluence, Mixpanel, Amplitude,
       Figma, SQL basics, REST APIs (integration expertise)

DOMAINS: Digital banking, mobile wallets, payment processing, BNPL, remittances,
         e-commerce, OTT streaming, telecoms, KYC/AML
"""


class RizwanAgent(BaseAgent):
    """
    Represents Rizwan in the agent dialogue.
    Responds to gap questions from CompanyAgent with specific evidence.
    """

    def __init__(self):
        settings = get_settings()
        super().__init__(name="RizwanAgent", model=settings.rizwan_agent_model)
        self.profile = self._load_profile()
        self._seeded = False

    def _load_profile(self) -> dict:
        try:
            with open(get_settings().profile_path) as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            return {}

    async def seed_supabase_profile(self):
        """
        Seed Rizwan's profile into Supabase pgvector if not already done.
        Only needs to run once (or when CV is updated).
        """
        if self._seeded:
            return

        sections = {
            "summary": f"""
Rizwan Zafar — Technical Programme Lead & CPO with 14+ years in digital payments and banking.
Current: Technical Programme Lead / CPO at SimPaisa, scaling Pakistan's fastest digital payment platform to $1B+ TPV.
Specialises in product strategy, programme governance, enterprise integrations, and Agile delivery.
Certifications: PMP, PMI-ACP, CSPO, CSM, COBIT 5, ITIL v3.
Targeting Head of Product, Senior PM, PMO Specialist roles in UAE, KSA, Qatar, UK.
""",
            "current_simpaisa": """
SimPaisa (Aug 2020 – Present) | Technical Programme Lead / CPO
- Scaled platform from 0 to $1B+ total payment volume
- Led 40+ engineers across 12 squads
- Fortune 500 integrations: TikTok, Uber, MoneyGram, PUBG
- Full RAID log management across 50+ dependencies, SteerCo ownership
- Launched BNPL: 0 to 100K users in 8 months
- Built SBP-compliant payment infrastructure (3DS, PCI-DSS)
- Programme reporting: monthly board decks, C-suite presentations
""",
            "daraz_experience": """
Daraz/Alibaba (2018–2020) | Senior Product Manager
- Pakistan's #1 e-commerce platform, Alibaba-owned, ~100M monthly sessions
- Led checkout, payments, seller portal product lines
- Delivered Alipay + local payment integrations
- Reduced checkout abandonment by 22% via A/B testing
- Ran Agile squads: 15 engineers, 3 designers
""",
            "pmo_experience": """
PMO Expertise — Governance at Scale:
- RAID log management: 50+ dependencies, risk registers, issue logs
- SteerCo ownership: prepared and presented at steering committee level
- MS Project: detailed programme plans with critical path analysis
- Think-cell: executive-quality presentation slides
- Bank Alfalah (2010-2016): digital banking transformation, core banking migration
- TapmadTV (2016-2018): $3M programme delivery, built PMO from scratch
- Programme reporting: weekly status, monthly board packs, escalation management
""",
            "certifications_skills": """
Certifications: PMP (Project Management Professional), PMI-ACP (Agile Certified Practitioner),
CSPO (Certified Scrum Product Owner), CSM (Certified Scrum Master), COBIT 5, ITIL v3

Technical: REST API integration (deep hands-on experience), payment gateways, 3DS/PCI-DSS,
mobile product (iOS/Android), SQL, data analytics (Mixpanel, Amplitude, Tableau)

Tools: MS Project, PowerPoint, Excel, Think-cell, Jira, Confluence, Figma, Miro, Notion

Domains: Fintech, Digital Banking, Mobile Wallets, BNPL, Remittances, E-commerce, OTT
""",
        }

        for section, content in sections.items():
            await upsert_rizwan_profile(section, content)

        self._seeded = True
        self.log("Rizwan profile seeded to Supabase ✅")

    async def respond_to_gap(
        self,
        gap: dict,
        company_name: str,
        jd_context: str,
        turn: int,
        job_id: int,
    ) -> str:
        """
        Given a gap identified by CompanyAgent, Rizwan responds with specific evidence.
        Uses semantic search of profile to find the most relevant experience.
        """
        # Semantic search: find most relevant parts of Rizwan's profile
        query = f"{gap.get('jd_requirement', '')} {gap.get('gap_description', '')}"
        relevant_profile = await search_rizwan_profile(query, match_count=4)
        relevant_stories = await search_story_bank(query, match_count=2)

        # Build context
        profile_context = "\n".join([
            f"[{r['section']}]: {r['content']}"
            for r in relevant_profile
        ])
        story_context = ""
        if relevant_stories:
            story_context = "\n".join([
                f"Story: {s['title']}\nAction: {s['action']}\nResult: {s['result']}"
                for s in relevant_stories
            ])

        system = f"""You are Rizwan Zafar, a senior product and programme leader.
{company_name} has identified a gap in your resume vs. their job requirements.
Your job is to provide SPECIFIC, HONEST evidence from your real experience.
Use exact numbers, projects, and outcomes. Don't exaggerate.
If you genuinely don't have the experience, say so clearly and offer the closest adjacent experience instead."""

        user = f"""Company {company_name} raised this gap:
Gap area: {gap.get('area', '')}
JD requirement: {gap.get('jd_requirement', '')}
Gap description: {gap.get('gap_description', '')}
Their question for you: {gap.get('question_for_rizwan', '')}

Your relevant experience:
{profile_context if profile_context else RIZWAN_EXPERIENCE_CONTEXT[:3000]}

{f"Relevant STAR stories:{chr(10)}{story_context}" if story_context else ""}

JD Context: {jd_context[:500]}

Respond as Rizwan — provide SPECIFIC evidence:
1. What specific project or experience addresses this?
2. What were the exact outcomes (numbers, scale, impact)?
3. What tools/frameworks/processes did you use?
4. Rate your confidence: strong fit (can speak to this for 10 minutes) | partial fit (can address it) | honest gap (don't have this)

Keep your response focused and evidence-based. Be honest."""

        response = await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=800,
            temperature=0.4,
        )

        # Save turn to DB
        save_conversation_turn(
            job_id=job_id,
            company=company_name,
            role=gap.get("area", ""),
            turn=turn,
            speaker="rizwan_agent",
            message=response,
            gap_filled=False,
        )

        return response

    async def generate_cover_email(
        self,
        job_title: str,
        company_name: str,
        company_hooks: list[str],
        tailoring_priorities: list[str],
        jd_summary: str
    ) -> str:
        """Generate a personalised cover email (< 250 words)."""

        hook = company_hooks[0] if company_hooks else f"{company_name}'s digital innovation"
        priorities = "\n".join(f"- {p}" for p in tailoring_priorities[:3])

        system = """You are Rizwan Zafar, writing a cover email for a job application.
Tone: confident, specific, no fluff. Under 250 words. End with a clear call to action.
Never start with 'I am writing to apply'. Never use the phrase 'I am passionate about'."""

        user = f"""Write a cover email for: {job_title} at {company_name}

Company hook to use: {hook}

Key strengths to highlight:
{priorities}

JD focus: {jd_summary[:500]}

My profile:
- 14+ years in digital payments and banking
- SimPaisa: $1B+ TPV, Fortune 500 integrations (TikTok, Uber, MoneyGram)
- Daraz/Alibaba: Pakistan's #1 e-commerce
- PMP, PMI-ACP, CSPO, CSM certified
- Currently in Dubai, open to {company_name}'s location

Write the email now. Include subject line as first line."""

        return await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=600,
            temperature=0.5,
        )

    async def reflect_on_application(
        self,
        company_name: str,
        job_title: str,
        outcome: str,
        notes: str
    ) -> str:
        """
        After an application result (interview, rejection, offer),
        capture learnings to improve future applications.
        """
        system = "You are helping Rizwan reflect on a job application outcome to improve future performance."
        user = f"""Application: {job_title} at {company_name}
Outcome: {outcome}
Notes: {notes}

Write a brief reflection (3-5 bullet points):
1. What worked well in this application?
2. What could have been stronger?
3. What to do differently next time?
4. Any skills/gaps to address?"""

        return await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=500,
            temperature=0.6,
        )

    async def run(self, gap: dict = None, company_name: str = "", **kwargs) -> str:
        """Default: respond to a gap from a company agent."""
        if gap:
            return await self.respond_to_gap(gap, company_name, "", 1, 0)
        await self.seed_supabase_profile()
        return "Rizwan Agent ready."
