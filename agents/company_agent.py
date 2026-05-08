"""
CompanyAgent — one instance per company, built once, reused forever.

Each CompanyAgent:
  1. Scrapes the company website, news, LinkedIn, Glassdoor, funding data
  2. Stores everything in Supabase pgvector (persistent)
  3. Acts as an internal company expert — knows their stack, culture, challenges
  4. Reviews Rizwan's resume and identifies gaps vs. the specific JD
  5. Engages in dialogue with RizwanAgent to get evidence that fills gaps
  6. Builds the final tailored resume once gaps are filled

This is the heart of the system — company agents live forever and get smarter.
"""

from __future__ import annotations
import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
import httpx
import yaml
from bs4 import BeautifulSoup
from tenacity import retry, stop_after_attempt, wait_exponential

from agents.base_agent import BaseAgent
from config.settings import get_settings
from db.client import (
    upsert_company_knowledge,
    search_company_knowledge,
    upsert_company,
    get_company_by_name,
    save_conversation_turn,
)

logger = logging.getLogger(__name__)


COMPANY_RESEARCH_SECTIONS = [
    "overview",    # what they do, business model, product
    "news",        # recent news, launches, funding rounds
    "funding",     # investors, total raised, valuation
    "culture",     # Glassdoor, work style, values
    "tech_stack",  # engineering blog, job ads, StackShare
    "strategy",    # roadmap, expansion plans, market position
    "challenges",  # known problems, market headwinds
    "competitors", # who they compete with
    # Recruitment intelligence (Phase D extension)
    "recruitment_process",  # interview stages, timeline, recruiter touch points
    "resume_dos_donts",     # what to highlight, what to drop, length, formatting
    "ats_signals",          # exact keywords/phrases their parser/screener favors
    "interview_format",     # behavioral / case / technical / panels
    "hiring_signals",       # what they value: pedigree, results, builder mindset, etc.
]


class CompanyAgent(BaseAgent):
    """
    Per-company AI expert. Built once, reused for every role at that company.
    Speaks to RizwanAgent to identify and fill resume gaps.
    """

    def __init__(self, company_name: str):
        settings = get_settings()
        super().__init__(
            name=f"CompanyAgent[{company_name}]",
            model=settings.company_agent_model
        )
        self.company_name = company_name
        self.company_record: Optional[dict] = None
        self.knowledge_loaded = False

    async def build_or_refresh(self, force: bool = False) -> dict:
        """
        Build (or refresh) company knowledge base.
        Idempotent — if knowledge has ALL expected sections and force=False, skips.
        Returns a summary of what was loaded.
        """
        self.log(f"Building knowledge for {self.company_name}...")
        # Check if we have ALL expected sections cached
        if not force:
            from db.client import get_supabase
            existing_rows = (
                get_supabase()
                .table("company_knowledge")
                .select("section")
                .eq("company_name", self.company_name)
                .execute()
                .data
            ) or []
            existing_sections = {r["section"] for r in existing_rows}
            expected = set(COMPANY_RESEARCH_SECTIONS)
            missing = expected - existing_sections
            if not missing:
                self.log(f"  Knowledge complete ({len(existing_sections)} sections), skipping re-scrape.")
                self.knowledge_loaded = True
                return {"status": "fresh", "company": self.company_name}
            else:
                self.log(f"  Missing {len(missing)} sections: {sorted(missing)} — refreshing.")

        # Scrape company intelligence
        intelligence = await self._research_company()

        # Store each section in Supabase with embeddings
        company_record = get_company_by_name(self.company_name)
        if not company_record:
            company_record = upsert_company({
                "name": self.company_name,
                "domain": intelligence.get("domain"),
                "industry": intelligence.get("industry"),
                "country": intelligence.get("country"),
                "stage": intelligence.get("stage"),
            })
        self.company_record = company_record
        company_id = company_record.get("id") if company_record else None

        stored_sections = []
        for section, content in intelligence.get("sections", {}).items():
            if content and len(content.strip()) > 50:
                await upsert_company_knowledge(
                    company_name=self.company_name,
                    company_id=company_id,
                    section=section,
                    content=content,
                    source_url=intelligence.get("sources", {}).get(section),
                )
                stored_sections.append(section)

        self.knowledge_loaded = True
        self.log(f"  ✅ Stored {len(stored_sections)} knowledge sections: {stored_sections}")
        return {"status": "built", "company": self.company_name, "sections": stored_sections}

    async def _research_company(self) -> dict:
        """
        Deep research on the company using Claude Opus 4.
        Uses web search (via Serper) + page scraping.
        """
        # Step 1: Find key URLs
        urls = await self._discover_company_urls()

        # Step 2: Scrape pages
        raw_content = {}
        for label, url in urls.items():
            if url:
                try:
                    raw_content[label] = await self._scrape_page(url)
                except Exception as e:
                    logger.debug(f"Scrape failed for {url}: {e}")

        # Step 3: Claude Opus 4 synthesizes everything
        system = f"""You are a deep company intelligence analyst.
You have scraped content from {self.company_name}'s website, news articles, and public sources.
Extract structured intelligence in JSON format."""

        user = f"""Research {self.company_name} and extract the following from this scraped content:

{json.dumps(raw_content, indent=2)[:8000]}

Return JSON with these keys:
{{
  "domain": "company website domain",
  "industry": "primary industry (Fintech | E-commerce | SaaS | etc)",
  "country": "headquarters country",
  "stage": "startup | scaleup | enterprise | public",
  "headcount": "rough employee count",
  "sections": {{
    "overview": "What {self.company_name} does, their core product, business model, revenue streams",
    "news": "Recent news: funding rounds, product launches, partnerships, leadership changes (last 12 months)",
    "funding": "Total raised, investors, latest round details, valuation if known",
    "culture": "Work culture, values, what employees say, hiring bar, team structure",
    "tech_stack": "Technologies used, engineering practices, product tech stack",
    "strategy": "Strategic direction, expansion plans, product roadmap signals, market position",
    "challenges": "Known challenges, market headwinds, competitive threats, what they're trying to solve",
    "competitors": "Main competitors and how {self.company_name} differentiates",
    "recruitment_process": "What does the typical recruitment funnel look like at {self.company_name}? Be specific: # of interview rounds, recruiter screen, hiring manager screen, panel/loop, take-home, executive review, offer. Include timeline (e.g. 4-6 weeks). Cite Glassdoor / employee reports if available.",
    "resume_dos_donts": "Concrete do's and don'ts when applying to {self.company_name}: what to emphasize on resume (e.g. quantified outcomes, specific tech, scale metrics), what to drop or de-emphasize, ideal resume length, formatting style (ATS-friendly vs. designed), cover letter expectations.",
    "ats_signals": "Exact keywords + phrases that their ATS parser or first-pass screener favors. Be specific to {self.company_name}'s language: copy phrasing from their JDs and About page (e.g. 'merchant of record', 'card-on-file', 'real-time payments', 'embedded finance'). List 15-25 distinct keywords.",
    "interview_format": "Format breakdown: behavioral interviews (which competencies?), case interviews (which scenarios?), technical assessment (live coding, take-home, system design?), panels (who attends?), executive round expectations.",
    "hiring_signals": "What does {self.company_name} actually value in candidates beyond the JD? Pedigree (FAANG / consulting / specific schools)? Results-driven track record (specific metrics they care about)? Founder/builder mindset? Domain expertise? Cultural alignment? Be candid and concrete."
  }},
  "sources": {{
    "overview": "URL or source",
    "news": "URL or source"
  }}
}}

Be specific. Use actual data from the content. Do NOT fabricate information.
If data is unavailable for a section, write "Unknown — insufficient data." """

        try:
            response = await self.ask_claude(
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=4000,
                temperature=0.2,
            )
            # Extract JSON
            start = response.find("{")
            end = response.rfind("}") + 1
            return json.loads(response[start:end])
        except Exception as e:
            logger.error(f"Claude research failed for {self.company_name}: {e}")
            return {"sections": {}, "sources": {}}

    async def _discover_company_urls(self) -> dict:
        """Find key URLs for the company using Serper.

        BUG-03 fix: guard against None serper_api_key — previously would crash
        with httpx.InvalidURL when the key is missing.
        BUG-04 fix: news query now uses current years (2025 2026).
        """
        settings = get_settings()

        # Guard: no Serper key = return empty dict, don't crash
        if not settings.serper_api_key:
            logger.debug(f"Serper key not set — skipping URL discovery for {self.company_name}")
            return {}

        queries = {
            "website": f"{self.company_name} official website careers",
            "news": f"{self.company_name} funding news 2025 2026",
            "glassdoor": f"{self.company_name} site:glassdoor.com reviews",
            "glassdoor_interviews": f"{self.company_name} site:glassdoor.com interviews",
            "linkedin": f"{self.company_name} site:linkedin.com/company",
            "blog": f"{self.company_name} engineering blog product blog",
            # Recruitment-intel sources
            "interview_reports": f"{self.company_name} interview process site:reddit.com OR site:blind.teamblind.com",
            "levels": f"{self.company_name} site:levels.fyi compensation",
            "ats_signals": f"{self.company_name} job description product manager payments",
            "hiring_bar": f"{self.company_name} hiring bar interview tips",
        }

        urls = {}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                for label, query in queries.items():
                    try:
                        resp = await client.post(
                            settings.serper_endpoint,
                            headers={
                                "X-API-KEY": settings.serper_api_key,
                                "Content-Type": "application/json"
                            },
                            json={"q": query, "num": 3}
                        )
                        data = resp.json()
                        results = data.get("organic", [])
                        if results:
                            urls[label] = results[0].get("link")
                        await asyncio.sleep(0.3)
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"URL discovery failed: {e}")

        return urls

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=5))
    async def _scrape_page(self, url: str) -> str:
        """Scrape a webpage and return clean text."""
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            headers = {"User-Agent": "Mozilla/5.0 (compatible; JobHuntBot/1.0)"}
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            # Remove scripts, styles, nav
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            text = soup.get_text(separator=" ", strip=True)
            return text[:5000]  # Limit per page

    async def build_resume_as_recruitment_expert(
        self,
        jd_text: str,
        rizwan_profile_text: str,
        job_title: str,
        archetype: Optional[str] = None,
    ) -> dict:
        """
        Workflow v2: invoked when user clicks "Generate Resume" on a job >= 85.
        Persona: Head of an expert recruitment agency advising on the BEST
        possible resume for THIS role at THIS company.

        Returns the full A-F evaluation + tailoring brief that downstream
        ResumeBuilderAgent uses.
        """
        company_context = await self._get_relevant_knowledge(jd_text)
        recruitment_intel = await self._get_recruitment_intel()

        system = f"""You are the Head of an Expert Recruitment Agency that has placed
hundreds of senior product leaders at {self.company_name} and similar fintechs.

You know {self.company_name}'s recruitment process inside out:
- How their ATS parses resumes
- What hiring managers scan for in the first 6 seconds
- Which keywords pass screening, which raise flags
- What story arcs and metrics resonate with their interview panels
- The do's and don'ts specific to {self.company_name}

You are now advising the candidate on how to position themselves for THIS specific role,
based on YOUR insider knowledge of {self.company_name}.

You speak as a trusted advisor — direct, concrete, no fluff. You only recommend
changes that are 100% truthful to the candidate's actual experience. You never invent.

Your output is a structured tailoring brief that downstream agents use to
generate the final resume."""

        user = f"""Role: {job_title}{f' (archetype: {archetype})' if archetype else ''}

═══ THE JOB DESCRIPTION ═══
{jd_text[:6000]}

═══ {self.company_name} — RECRUITMENT INTEL (your insider knowledge) ═══
{recruitment_intel[:3500]}

═══ {self.company_name} — STRATEGIC CONTEXT ═══
{company_context[:3500]}

═══ THE CANDIDATE'S ACTUAL EXPERIENCE ═══
{rizwan_profile_text[:5000]}

═══ YOUR TASK ═══
Produce a structured A-F tailoring brief in JSON. Be specific to {self.company_name}'s style.

Return JSON with these blocks:
{{
  "block_a_role_summary": {{
    "tldr": "1-sentence positioning of why the candidate fits this role",
    "level_signal": "level we're targeting and why (Senior PM / Head of / VP)",
    "key_themes": ["theme1", "theme2", "theme3"]
  }},
  "block_b_match": {{
    "matched_requirements": [
      {{"jd_requirement": "...", "candidate_evidence": "...", "strength": "high|medium|low"}}
    ],
    "gaps": [
      {{"requirement": "...", "severity": "blocker|gap|nice_to_have", "mitigation": "specific phrase or angle"}}
    ]
  }},
  "block_c_level_strategy": {{
    "target_level": "...",
    "selling_phrases": ["phrase that positions seniority without overclaiming", "..."],
    "if_downleveled_plan": "what to negotiate if they offer a lower level"
  }},
  "block_d_comp_signals": {{
    "expected_range": "based on level + location + company stage",
    "negotiation_anchors": ["specific leverage points based on candidate's record"]
  }},
  "block_e_personalization": {{
    "summary_rewrite": "3-line professional summary tailored for this exact role at {self.company_name}",
    "top_5_resume_changes": [
      {{"section": "...", "current": "...", "tailored": "...", "why": "..."}}
    ],
    "ats_keywords_to_inject": ["keyword1", "keyword2", "..."],
    "achievements_to_lead_with": ["...", "..."]
  }},
  "block_f_interview_prep": {{
    "likely_questions_with_star": [
      {{"question": "expected behavioral or case Q", "star_situation": "...", "task": "...", "action": "...", "result": "...", "reflection": "what was learned"}}
    ],
    "red_flag_questions": [
      {{"question": "tricky Q they'll ask", "framed_answer": "..."}}
    ],
    "case_to_pitch": "1 specific candidate project to lead with and why"
  }},
  "company_hooks": ["3-5 specific hooks tying candidate's record to {self.company_name}'s strategic priorities"],
  "tailoring_priorities": ["ordered list of 5 things to fix on the resume in priority order"]
}}

Be concrete. Use real phrases. No fluff. JSON only."""

        try:
            response = await self.ask_claude(
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=6000,
                temperature=0.25,
            )
            start = response.find("{")
            end = response.rfind("}") + 1
            return json.loads(response[start:end])
        except Exception as e:
            logger.error(f"Resume-mode brief failed for {self.company_name}: {e}")
            return {"error": str(e)}

    async def _get_recruitment_intel(self) -> str:
        """Pull just the recruitment-intel sections of company_knowledge."""
        from db.client import get_supabase
        sections = ("recruitment_process", "resume_dos_donts", "ats_signals",
                    "interview_format", "hiring_signals")
        try:
            rows = (
                get_supabase()
                .table("company_knowledge")
                .select("section, content")
                .eq("company_name", self.company_name)
                .in_("section", list(sections))
                .execute()
                .data
            ) or []
            if not rows:
                return "No recruitment intel cached. Apply general best practices."
            return "\n\n".join(f"[{r['section']}]\n{r['content']}" for r in rows)
        except Exception as e:
            logger.debug(f"Recruitment intel fetch failed: {e}")
            return ""

    async def review_resume_against_jd(
        self,
        jd_text: str,
        rizwan_profile_text: str,
        job_title: str
    ) -> dict:
        """
        Core function: review Rizwan's profile vs. the JD.
        Identify gaps, matched strengths, and tailoring opportunities.
        Returns structured gap analysis.
        """
        # Pull relevant company knowledge
        company_context = await self._get_relevant_knowledge(jd_text)

        system = f"""You are the internal Head of Talent at {self.company_name}.
You know the company deeply — its culture, technical expectations, business priorities, and what makes candidates succeed here.
You are reviewing Rizwan Zafar's profile against a job description for a {job_title} role.
Be direct, honest, and specific. Your job is to identify exactly what's missing and what's strong."""

        user = f"""## Company Context
{company_context}

## Job Description
{jd_text[:3000]}

## Rizwan's Profile
{rizwan_profile_text[:3000]}

## Your Task
Analyze the fit and produce a JSON response:
{{
  "overall_match_score": 0-100,
  "strengths": [
    {{"area": "...", "evidence": "specific from Rizwan's profile", "relevance": "why this matters for the role"}}
  ],
  "gaps": [
    {{
      "area": "...",
      "jd_requirement": "exact requirement from JD",
      "gap_severity": "critical | important | minor",
      "gap_description": "what's missing",
      "question_for_rizwan": "specific question to ask Rizwan to check if this gap can be filled"
    }}
  ],
  "tailoring_priorities": [
    "Top 3-5 things to emphasize in the resume and cover letter"
  ],
  "company_hooks": [
    "Specific company news/facts to reference in cover letter for credibility"
  ],
  "red_flags": [
    "Anything that could disqualify — be honest"
  ]
}}"""

        response = await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=3000,
            temperature=0.3,
        )
        start = response.find("{")
        end = response.rfind("}") + 1
        return json.loads(response[start:end])

    async def respond_to_rizwan_evidence(
        self,
        gap: dict,
        rizwan_evidence: str,
        turn: int,
        job_id: int
    ) -> dict:
        """
        CompanyAgent receives Rizwan's evidence for a gap and evaluates it.
        Returns whether the gap is filled and what bullet point to add to resume.
        """
        company_context_brief = await self._get_relevant_knowledge(gap.get("jd_requirement", ""))

        system = f"""You are the internal Head of Talent at {self.company_name}.
Rizwan just provided evidence to address a gap you identified.
Evaluate the evidence honestly — does it actually fill the gap?
If yes, craft the exact resume bullet point that should be added.
If partial, ask one follow-up question to get stronger evidence.
If no, explain why it doesn't fill the gap."""

        user = f"""Gap you identified:
{json.dumps(gap, indent=2)}

Evidence Rizwan provided:
{rizwan_evidence}

Company context:
{company_context_brief}

Respond in JSON:
{{
  "verdict": "filled | partial | not_filled",
  "evaluation": "your assessment of the evidence",
  "resume_bullet": "If filled/partial: the exact bullet point for the resume (quantified, action verb, results)",
  "follow_up_question": "If partial: one specific follow-up question",
  "why_not_filled": "If not_filled: explain what's still missing"
}}"""

        response = await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=1500,
            temperature=0.3,
        )

        # Save to conversation history
        save_conversation_turn(
            job_id=job_id,
            company=self.company_name,
            role=gap.get("area", ""),
            turn=turn,
            speaker="company_agent",
            message=response,
            gap_identified=gap.get("gap_description"),
        )

        start = response.find("{")
        end = response.rfind("}") + 1
        return json.loads(response[start:end])

    async def build_final_resume_brief(
        self,
        job_title: str,
        jd_text: str,
        original_profile: str,
        filled_gaps: list[dict],
    ) -> str:
        """
        Final step: CompanyAgent creates a complete tailoring brief for the resume.
        """
        company_context = await self._get_relevant_knowledge(jd_text)
        gap_bullets = "\n".join([
            f"- {g['area']}: {g.get('resume_bullet', '')}"
            for g in filled_gaps if g.get('resume_bullet')
        ])

        system = f"""You are an expert executive resume writer who knows {self.company_name} intimately.
Create a complete tailoring brief that tells the Resume Writer exactly what to write."""

        user = f"""Job: {job_title} at {self.company_name}

Company Context:
{company_context}

JD (key requirements):
{jd_text[:2000]}

Original Profile:
{original_profile[:2000]}

New bullets from gap-filling dialogue:
{gap_bullets}

Produce a TAILORING BRIEF with:
1. SUMMARY PARAGRAPH (3-4 sentences, opens with the most relevant achievement, mentions {self.company_name} context)
2. TOP 8 SKILLS TO LEAD WITH (in priority order for this JD)
3. EXPERIENCE BULLETS TO EMPHASISE (top 5-7 from Rizwan's history, maximally relevant)
4. NEW BULLETS TO ADD (from the gap-filling dialogue above)
5. COVER EMAIL HOOK (one sentence using a specific company fact for credibility)
6. WHAT TO OMIT (anything that dilutes the fit signal)"""

        return await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=3000,
            temperature=0.4,
        )

    async def _get_relevant_knowledge(self, query: str, sections: int = 4) -> str:
        """Pull the most relevant company knowledge for a given query."""
        results = await search_company_knowledge(
            self.company_name, query, match_count=sections
        )
        if not results:
            return f"No stored knowledge for {self.company_name} yet."
        parts = []
        for r in results:
            parts.append(f"[{r['section'].upper()}]\n{r['content']}")
        return "\n\n".join(parts)

    async def run(self, *args, **kwargs):
        """Default run: build/refresh company knowledge."""
        return await self.build_or_refresh()
