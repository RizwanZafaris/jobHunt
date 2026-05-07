"""
InterviewAgent — generates company-specific interview prep.

For each high-scoring job, produces:
  1. Likely interview questions (from Glassdoor, Blind, JD analysis)
  2. STAR+Reflection answers mapped to Rizwan's stories
  3. Technical/domain questions specific to the role
  4. Red flag questions (gaps they might probe)
  5. Salary negotiation script
  6. 30/60/90-day plan for the role
  7. Questions to ask the interviewer
"""

from __future__ import annotations
import json
import logging
from pathlib import Path
from typing import Optional
import yaml
import httpx

from agents.base_agent import BaseAgent
from config.settings import get_settings
from supabase.client import search_company_knowledge, search_story_bank, search_rizwan_profile

logger = logging.getLogger(__name__)


class InterviewAgent(BaseAgent):
    """Generates company-specific interview prep packs."""

    def __init__(self):
        settings = get_settings()
        super().__init__(name="InterviewAgent", model=settings.interview_agent_model)
        self.profile = self._load_profile()

    def _load_profile(self) -> dict:
        try:
            with open(get_settings().profile_path) as f:
                return yaml.safe_load(f)
        except FileNotFoundError:
            return {}

    async def run(
        self,
        company: str,
        job_title: str,
        jd_text: str,
        job_id: Optional[int] = None
    ) -> dict:
        """Generate complete interview prep for a job."""
        self.log(f"Preparing interview pack for {job_title} @ {company}")

        # 1. Research interview process
        process_intel = await self._research_interview_process(company, job_title)

        # 2. Get company context
        company_context = await self._get_company_context(company, jd_text)

        # 3. Generate likely questions
        questions = await self._generate_likely_questions(
            company, job_title, jd_text, process_intel
        )

        # 4. Map STAR stories
        story_map = await self._map_stories_to_questions(questions)

        # 5. Technical prep checklist
        tech_checklist = await self._generate_tech_checklist(jd_text, process_intel)

        # 6. Red flag prep
        red_flags = await self._prepare_red_flag_answers(company, job_title)

        # 7. Negotiation script
        negotiation = await self._generate_negotiation_script(company, job_title)

        # 8. Questions to ask
        questions_to_ask = await self._generate_questions_to_ask(company, jd_text)

        # 9. 30/60/90-day plan
        plan_90 = await self._generate_90_day_plan(company, job_title, jd_text)

        result = {
            "company": company,
            "job_title": job_title,
            "process_intel": process_intel,
            "company_context_summary": company_context[:500],
            "likely_questions": questions,
            "story_mappings": story_map,
            "tech_checklist": tech_checklist,
            "red_flag_prep": red_flags,
            "negotiation_script": negotiation,
            "questions_to_ask": questions_to_ask,
            "plan_90_days": plan_90,
        }

        # Save to file
        output_path = self._save_prep_pack(result, company, job_title)
        result["file_path"] = output_path

        self.log(f"✅ Interview prep saved: {output_path}", style="green")
        return result

    async def _research_interview_process(self, company: str, role: str) -> dict:
        """Research the interview process from Glassdoor, Blind, etc."""
        settings = get_settings()
        queries = [
            f'"{company}" "{role}" interview questions site:glassdoor.com',
            f'"{company}" interview process site:teamblind.com',
            f'"{company}" interview experience {role} 2024 2025',
        ]

        raw_results = []
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                for query in queries:
                    try:
                        resp = await client.post(
                            settings.serper_endpoint,
                            headers={
                                "X-API-KEY": settings.serper_api_key,
                                "Content-Type": "application/json"
                            },
                            json={"q": query, "num": 5}
                        )
                        data = resp.json()
                        for r in data.get("organic", [])[:3]:
                            raw_results.append(r.get("snippet", ""))
                    except Exception:
                        pass
        except Exception as e:
            logger.debug(f"Interview research search failed: {e}")

        snippets = "\n".join(raw_results[:10])

        system = "You are an interview intelligence analyst. Extract structured data from search results."
        user = f"""Extract interview process data for {company} {role} from these snippets:
{snippets[:3000]}

Return JSON:
{{
  "rounds": ["round 1 description", "round 2", ...],
  "duration": "typical timeline",
  "difficulty": "1-5",
  "positive_rate": "% positive experience if known",
  "reported_questions": ["actual question from source", ...],
  "tech_focus": ["what they test technically"],
  "behavioral_focus": ["what leadership/behavioral topics come up"],
  "known_quirks": ["any unusual things about their process"],
  "sources": ["glassdoor", "blind", etc]
}}
If data is sparse, say "Unknown" not fabricated data."""

        try:
            response = await self.ask_claude(
                system=system,
                messages=[{"role": "user", "content": user}],
                max_tokens=1500,
                temperature=0.2,
            )
            start = response.find("{")
            end = response.rfind("}") + 1
            return json.loads(response[start:end])
        except Exception:
            return {"rounds": ["Unknown"], "difficulty": "Unknown", "reported_questions": []}

    async def _get_company_context(self, company: str, jd_text: str) -> str:
        results = await search_company_knowledge(company, jd_text, match_count=3)
        if not results:
            return f"Company context for {company} not yet researched."
        return "\n".join([r["content"] for r in results])

    async def _generate_likely_questions(
        self, company: str, role: str, jd: str, process: dict
    ) -> dict:
        """Generate categorised likely questions."""
        reported = process.get("reported_questions", [])
        behavioral_focus = process.get("behavioral_focus", [])

        system = f"""You are an expert interviewer at {company} for {role} positions.
Generate likely interview questions categorised by type.
For each question, note what the interviewer is ACTUALLY testing (not just what it says on the surface)."""

        user = f"""Generate interview questions for {role} at {company}.

JD key requirements:
{jd[:1500]}

Already reported questions from research:
{json.dumps(reported)}

Behavioral focus areas identified:
{json.dumps(behavioral_focus)}

Rizwan's background to consider:
- 14+ years, currently Technical Programme Lead / CPO at SimPaisa
- Strong: payments, fintech, programme governance, enterprise integrations
- Notable: gap from pure corporate enterprise → startup/scaleup environment

Generate JSON:
{{
  "technical": [
    {{"question": "...", "what_they_test": "...", "source": "reported | inferred_from_jd"}}
  ],
  "behavioral": [
    {{"question": "...", "what_they_test": "...", "best_story_theme": "leadership | delivery | conflict | failure | product_vision"}}
  ],
  "role_specific": [
    {{"question": "...", "what_they_test": "...", "jd_requirement": "which JD bullet this maps to"}}
  ],
  "red_flag_probes": [
    {{"question": "...", "why_they_ask": "what concern are they probing", "recommended_framing": "how to answer honestly and well"}}
  ]
}}"""

        response = await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=3000,
            temperature=0.4,
        )
        start = response.find("{")
        end = response.rfind("}") + 1
        try:
            return json.loads(response[start:end])
        except Exception:
            return {"behavioral": [], "technical": [], "role_specific": [], "red_flag_probes": []}

    async def _map_stories_to_questions(self, questions: dict) -> list[dict]:
        """Map STAR stories to behavioral questions."""
        behavioral = questions.get("behavioral", [])
        if not behavioral:
            return []

        mappings = []
        for q in behavioral[:8]:  # Top 8 behavioral questions
            topic = f"{q.get('question', '')} {q.get('best_story_theme', '')}"
            stories = await search_story_bank(topic, match_count=2)

            best_story = stories[0] if stories else None
            mappings.append({
                "question": q.get("question"),
                "what_they_test": q.get("what_they_test"),
                "best_story": best_story.get("title") if best_story else "No existing story — see suggestion below",
                "story_similarity": best_story.get("similarity", 0) if best_story else 0,
                "star_answer": best_story if best_story else None,
                "story_suggestion": self._suggest_story(q) if not best_story else None,
            })
        return mappings

    def _suggest_story(self, question: dict) -> str:
        theme = question.get("best_story_theme", "delivery")
        suggestions = {
            "leadership": "Consider: Leading the 12-squad 40-engineer org at SimPaisa through a critical delivery",
            "delivery": "Consider: Delivering the TikTok or MoneyGram Fortune 500 integration under timeline pressure",
            "conflict": "Consider: Navigating stakeholder conflicts during BNPL product launch at SimPaisa",
            "failure": "Consider: A feature that didn't get the adoption expected and what you learned",
            "product_vision": "Consider: How you defined SimPaisa's product strategy from 0 to $1B TPV",
        }
        return suggestions.get(theme, "Draw from SimPaisa $1B journey or Daraz checkout optimization")

    async def _generate_tech_checklist(self, jd: str, process: dict) -> list[dict]:
        """Generate technical prep checklist based on what they actually test."""
        tech_focus = process.get("tech_focus", [])
        system = "You are an expert technical interviewer. Generate a targeted prep checklist."
        user = f"""Generate a technical prep checklist for this role.

JD requirements:
{jd[:1000]}

Known technical focus from research:
{json.dumps(tech_focus)}

For each item, give:
- Topic
- Why (evidence from JD or research)
- How to prepare (specific action, not generic advice)
- Rizwan's current strength (strong | moderate | needs prep)

Return as JSON array."""

        response = await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=1500,
            temperature=0.3,
        )
        try:
            start = response.find("[")
            end = response.rfind("]") + 1
            return json.loads(response[start:end])
        except Exception:
            return []

    async def _prepare_red_flag_answers(self, company: str, role: str) -> list[dict]:
        """Prepare answers for likely red flag questions about Rizwan's background."""
        system = "You are a career coach helping Rizwan prepare for tough questions about his background."
        user = f"""Rizwan Zafar is interviewing for {role} at {company}.

His background has these potential red flags that interviewers might probe:
1. Based in Pakistan/Dubai — company may be in KSA/UK/Qatar
2. Most experience in Pakistan startups vs. large enterprise environments
3. Dual CPO + Programme Lead title — might seem unfocused
4. Physics background, not traditional MBA or CS degree
5. 5+ years at one company (SimPaisa) — some might see this as lack of breadth

For each potential red flag, provide:
- The likely question they'll ask
- Recommended framing (honest, forward-looking, strengths-based)
- What to avoid saying
- Example strong answer opening sentence

Return as JSON array."""

        response = await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=2000,
            temperature=0.4,
        )
        try:
            start = response.find("[")
            end = response.rfind("]") + 1
            return json.loads(response[start:end])
        except Exception:
            return []

    async def _generate_negotiation_script(self, company: str, job_title: str) -> str:
        """Generate salary negotiation talking points."""
        comp = self.profile.get("compensation", {})
        system = "You are a senior compensation negotiation coach."
        user = f"""Rizwan is negotiating compensation for {job_title} at {company}.

His target range: {comp.get('target_range', 'AED 40-60K/month')}
His minimum: {comp.get('minimum', 'AED 35K/month')}

Generate a negotiation script covering:
1. How to respond when asked "What are your salary expectations?"
2. What to say when the offer comes in low
3. How to counter-offer professionally
4. Geographic/market discount pushback (if they try to lowball based on Pakistan background)
5. Total comp framing (base + bonus + equity)
6. Walk-away language if they can't meet minimum

Keep it practical and specific to this seniority level."""

        return await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=1000,
            temperature=0.4,
        )

    async def _generate_questions_to_ask(self, company: str, jd: str) -> list[str]:
        """Generate smart questions to ask the interviewer."""
        company_context = await self._get_company_context(company, jd)
        system = "You are a senior candidate strategically using questions to both learn and impress."
        user = f"""Generate 8-10 smart questions for Rizwan to ask at the {company} interview.

Company context:
{company_context[:1000]}

JD focus:
{jd[:800]}

Questions should:
- Demonstrate strategic thinking (not just "what does success look like")
- Show Rizwan did his homework on the company
- Uncover real information about the role and team
- Be appropriate for a senior leader level

Format: JSON array of strings."""

        response = await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=800,
            temperature=0.5,
        )
        try:
            start = response.find("[")
            end = response.rfind("]") + 1
            return json.loads(response[start:end])
        except Exception:
            return []

    async def _generate_90_day_plan(self, company: str, role: str, jd: str) -> str:
        """Generate a 30/60/90-day plan to use in the interview."""
        company_context = await self._get_company_context(company, jd)
        system = "You are a senior PM writing their 30/60/90-day plan for an interview."
        user = f"""Write a 30/60/90-day plan for Rizwan stepping into {role} at {company}.

Company context:
{company_context[:1000]}

JD priorities:
{jd[:800]}

Format:
- 30 days: Listen, learn, quick wins
- 60 days: Identify priorities, build relationships, produce first analysis
- 90 days: Drive first meaningful initiative, prove fit

Be specific to {company}'s actual business context."""

        return await self.ask_claude(
            system=system,
            messages=[{"role": "user", "content": user}],
            max_tokens=800,
            temperature=0.5,
        )

    def _save_prep_pack(self, result: dict, company: str, role: str) -> str:
        """Save the interview prep pack to a markdown file."""
        settings = get_settings()
        import os
        os.makedirs(settings.output_interview_dir, exist_ok=True)

        from datetime import date
        date_str = date.today().isoformat()
        filename = f"{company.lower().replace(' ', '-')}-{role.lower().replace(' ', '-')[:20]}-{date_str}.md"
        path = f"{settings.output_interview_dir}/{filename}"

        with open(path, "w") as f:
            f.write(f"# Interview Prep: {result['job_title']} @ {result['company']}\n\n")
            f.write(f"Generated: {date_str}\n\n")

            f.write("## Process Intelligence\n")
            f.write(f"```json\n{json.dumps(result['process_intel'], indent=2)}\n```\n\n")

            f.write("## Likely Questions\n")
            q = result.get("likely_questions", {})
            for category in ["behavioral", "technical", "role_specific", "red_flag_probes"]:
                items = q.get(category, [])
                if items:
                    f.write(f"\n### {category.replace('_', ' ').title()}\n")
                    for item in items:
                        f.write(f"\n**Q: {item.get('question', '')}**\n")
                        f.write(f"*What they test: {item.get('what_they_test', '')}*\n")

            f.write("\n## STAR Story Mappings\n")
            for mapping in result.get("story_mappings", []):
                f.write(f"\n**Q:** {mapping.get('question', '')}\n")
                f.write(f"**Best story:** {mapping.get('best_story', '')}\n")
                if mapping.get("story_suggestion"):
                    f.write(f"**Suggestion:** {mapping['story_suggestion']}\n")

            f.write(f"\n## Negotiation Script\n{result.get('negotiation_script', '')}\n\n")
            f.write(f"## Questions to Ask\n")
            for q_str in result.get("questions_to_ask", []):
                f.write(f"- {q_str}\n")
            f.write(f"\n## 30/60/90 Day Plan\n{result.get('plan_90_days', '')}\n")

            f.write(f"\n## Red Flag Prep\n")
            for rf in result.get("red_flag_prep", []):
                if isinstance(rf, dict):
                    f.write(f"\n**Q:** {rf.get('question', '')}\n")
                    f.write(f"**Framing:** {rf.get('recommended_framing', '')}\n")

        return path
