"""
NetworkingAgent — LinkedIn outreach and referral optimization.

Identifies warm connections at target companies, drafts personalized outreach,
and tracks networking activity. Increases interview conversion rate by 3-5x
compared to cold applications.
"""

from __future__ import annotations
import logging
from typing import Optional
import httpx
from datetime import date

from agents.base_agent import BaseAgent
from config.settings import get_settings

logger = logging.getLogger(__name__)


class NetworkingAgent(BaseAgent):
    """
    Finds connections and drafts outreach for target companies.

    Strategies:
    1. 2nd-degree LinkedIn connections at company
    2. Alumni from Rizwan's previous companies now at target
    3. PM community connections (ProductHunt, Mind the Product)
    4. Cold outreach to CPO/Head of Product (if company has no 2nd degrees)
    """

    RIZWAN_CONTEXT = """
Rizwan Zafar is a Technical Programme Lead / CPO based in Dubai, UAE.
Background: 14 years in fintech / payments / digital banking.
Currently: SimPaisa CPO, scaled $1B+ TPV, led 40+ engineers.
Ex: Daraz/Alibaba (Senior PM), TapmadTV (Programme Manager), PMO Consultant (UAE/Pakistan).
Certs: PMP, PMI-ACP, CSPO, CSM.
Tone: Professional, direct, not sycophantic. Mentions specific shared context.
Connection angle: "Fellow payments/fintech professional" — mentions real overlap.
"""

    OUTREACH_TEMPLATES = {
        "2nd_degree_referral": """
Subject: Quick question re: {role} at {company}

Hi {name},

I noticed we're connected through {mutual_connection} — hope you don't mind me reaching out directly.

I'm exploring the {role} opportunity at {company} and would love a 10-minute call to hear your perspective on the team and what makes someone successful there.

My background: CPO at SimPaisa (Pakistan's digital payments platform, $1B+ TPV), ex-Daraz/Alibaba. {specific_connection_hook}

Would you be open to a quick chat this week?

Best,
Rizwan
""",
        "cold_pm_community": """
Subject: Fellow PM — {company}'s payments roadmap

Hi {name},

{personalisation_hook}

I'm building out my network in the Gulf fintech space and your work at {company} on {specific_thing} caught my attention.

Background: CPO at SimPaisa (scaled Pakistan's digital wallet to $1B+ TPV with TikTok, Uber, MoneyGram integrations), now exploring senior roles in UAE/Gulf.

Would love to connect if you're open to it.

Rizwan
""",
        "hiring_manager_direct": """
Subject: {role} application — SimPaisa CPO ($1B TPV, TikTok/Uber integrations)

Hi {name},

I'm applying for the {role} at {company} and wanted to reach out directly.

In short: I've spent 14 years in fintech/payments, most recently as CPO of SimPaisa — scaled from 0 to $1B+ TPV in 4 years, delivered 4 Fortune 500 integrations (TikTok, Uber, MoneyGram, PUBG), and led 40+ engineers.

{company_specific_hook}

Happy to send a tailored resume or jump on a 15-minute call at your convenience.

Rizwan Zafar
rizwanzaffar.pk@gmail.com | +971-58-9683970
""",
    }

    def __init__(self):
        settings = get_settings()
        super().__init__(name="NetworkingAgent", model=settings.rizwan_agent_model)

    async def run(
        self,
        company: str,
        job_title: str,
        job_url: Optional[str] = None,
        hiring_manager_name: Optional[str] = None,
        company_context: Optional[str] = None,
    ) -> dict:
        """
        Generate networking strategy and outreach messages for a company.

        Returns:
            dict with outreach_messages, linkedin_search_queries, networking_strategy
        """
        self.log(f"Generating networking strategy for {job_title} @ {company}")

        # Research who to contact
        contacts = await self._identify_contacts(company, job_title)

        # Draft outreach messages
        messages = await self._draft_outreach(
            company=company,
            job_title=job_title,
            contacts=contacts,
            company_context=company_context or "",
            hiring_manager_name=hiring_manager_name,
        )

        # LinkedIn search queries
        queries = self._build_linkedin_queries(company, job_title)

        # Networking strategy
        strategy = await self._build_strategy(company, job_title, contacts)

        return {
            "company": company,
            "job_title": job_title,
            "contacts_to_find": contacts,
            "outreach_messages": messages,
            "linkedin_queries": queries,
            "strategy": strategy,
            "generated_date": date.today().isoformat(),
        }

    async def _identify_contacts(self, company: str, job_title: str) -> list[dict]:
        """Identify who to reach out to at the company."""
        system = "You are a networking strategist for a senior PM/CPO job seeker."
        user = f"""For a senior PM/CPO applying to {job_title} at {company}:

Identify the top 5 people to reach out to, in priority order.
Consider:
- Hiring manager (Head of Product, CPO, or equivalent)
- Team members (PMs, Engineering leads in the relevant team)
- HR/Recruiting (if no warm connections)
- Alumni connections (people who worked at Daraz, SimPaisa, TapmadTV)

Return JSON array:
[
  {{
    "role": "CPO / Head of Product",
    "why_valuable": "Direct hiring decision maker",
    "how_to_find": "LinkedIn search: site:linkedin.com {company} CPO",
    "outreach_template": "hiring_manager_direct",
    "priority": 1
  }},
  ...
]"""

        try:
            response = await self.ask_claude(
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=1500,
                temperature=0.3,
            )
            import json
            start = response.find("[")
            end = response.rfind("]") + 1
            return json.loads(response[start:end])
        except Exception as e:
            logger.warning(f"Contact identification failed: {e}")
            return [
                {
                    "role": "Head of Product / CPO",
                    "why_valuable": "Hiring decision maker",
                    "how_to_find": f"LinkedIn search: {company} Head of Product",
                    "outreach_template": "hiring_manager_direct",
                    "priority": 1,
                }
            ]

    async def _draft_outreach(
        self,
        company: str,
        job_title: str,
        contacts: list[dict],
        company_context: str,
        hiring_manager_name: Optional[str],
    ) -> list[dict]:
        """Draft personalised outreach messages."""
        messages = []

        for contact in contacts[:3]:  # Top 3 contacts
            template_key = contact.get("outreach_template", "hiring_manager_direct")
            template = self.OUTREACH_TEMPLATES.get(
                template_key, self.OUTREACH_TEMPLATES["hiring_manager_direct"]
            )

            # Use Claude to personalize the template
            system = f"You are Rizwan Zafar writing a professional outreach message. {self.RIZWAN_CONTEXT}"
            user = f"""Personalize this outreach template for {contact['role']} at {company}:

Template:
{template}

Company context: {company_context[:300] if company_context else 'N/A'}
Job title being applied for: {job_title}
Contact role: {contact['role']}
{'Hiring manager name: ' + hiring_manager_name if hiring_manager_name else ''}

Instructions:
- Make it specific to {company} and their actual business
- Keep it under 150 words for cold messages, 200 words for warm
- Don't be sycophantic ("I love your company!")
- Mention one specific thing about {company} that shows you've done research
- Sound like a peer, not an applicant
- Return just the message, no commentary"""

            try:
                msg = await self.ask_claude(
                    system=system,
                    messages=[{"role": "user", "content": user}],
                    max_tokens=500,
                    temperature=0.5,
                )
                messages.append({
                    "contact_role": contact["role"],
                    "template_used": template_key,
                    "message": msg.strip(),
                    "channel": "LinkedIn DM" if "2nd" in template_key else "Email/LinkedIn",
                })
            except Exception as e:
                logger.warning(f"Outreach drafting failed for {contact['role']}: {e}")

        return messages

    def _build_linkedin_queries(self, company: str, job_title: str) -> list[str]:
        """Build LinkedIn search queries to find the right contacts."""
        company_lower = company.lower()
        return [
            f'site:linkedin.com/in "{company}" "Head of Product"',
            f'site:linkedin.com/in "{company}" "Chief Product Officer"',
            f'site:linkedin.com/in "{company}" "Product Manager" "Senior"',
            f'site:linkedin.com/in "{company}" "Technical Programme Manager"',
            f'site:linkedin.com/in "{company}" "Engineering Manager" payments',
            f'"{company}" "Head of Product" site:linkedin.com',
            f'"{company_lower}" product manager linkedin',
        ]

    async def _build_strategy(
        self, company: str, job_title: str, contacts: list[dict]
    ) -> str:
        """Generate a week-by-week networking strategy."""
        system = "You are a senior career coach for fintech executives."
        user = f"""Build a 2-week networking strategy for Rizwan applying to {job_title} at {company}.

Context: Rizwan is a CPO/Technical PM with $1B+ TPV experience. He's applying in a competitive market.
Contacts identified: {[c['role'] for c in contacts]}

Produce a practical day-by-day action plan covering:
Week 1:
- Day 1-2: Research and prep
- Day 3-4: LinkedIn connection requests (personalized)
- Day 5-7: Follow up on pending connections

Week 2:
- Outreach follow-ups
- Community engagement (ProductHunt, Mind the Product Discord, LinkedIn posts)
- Internal referral strategy

Be specific and actionable. Keep it under 300 words."""

        return await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=600,
            temperature=0.4,
        )

    async def generate_linkedin_post(
        self, achievement: str, industry_hook: str
    ) -> str:
        """
        Generate a LinkedIn thought leadership post to increase visibility.
        Rizwan posting about payments/fintech keeps him top-of-mind with recruiters.
        """
        system = f"You are Rizwan Zafar writing a LinkedIn post. {self.RIZWAN_CONTEXT}"
        user = f"""Write a LinkedIn post about: {achievement}
Industry angle: {industry_hook}

Requirements:
- 150-250 words
- Personal story format: challenge → action → result
- End with a question to drive comments
- No corporate jargon or buzzwords
- Sounds like a real practitioner sharing learnings, not marketing
- Include 3-4 relevant hashtags (payments, fintech, productmanagement, UAE)"""

        return await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=400,
            temperature=0.6,
        )
