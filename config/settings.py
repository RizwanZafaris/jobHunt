"""
Central settings — loaded once at startup.
All config comes from environment variables or .env file.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # ── LLM Provider Keys ──────────────────────────────────────────────────
    # Phase 0: multi-LLM router supports 5 providers. Anthropic + OpenAI are
    # required (existing); the others are optional and only loaded when used.
    anthropic_api_key: str = Field(..., env="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(..., env="OPENAI_API_KEY")
    google_api_key: Optional[str] = Field(None, env="GOOGLE_API_KEY")       # Gemini
    deepseek_api_key: Optional[str] = Field(None, env="DEEPSEEK_API_KEY")   # V3 + R1
    kimi_api_key: Optional[str] = Field(None, env="KIMI_API_KEY")           # Moonshot K2

    # ── Agent model assignments (Phase 0: defaults preserve current behavior) ──
    # Each agent has (provider, model). Provider can be inferred from the
    # model name via agents/llm_router.py:infer_provider() — pass it
    # explicitly only when you want to override.
    # BUG-01 fix: correct Anthropic model strings (claude-opus-4-5 was invalid)
    company_agent_model: str = "claude-opus-4-5-20251101"    # Claude Opus 4.5
    rizwan_agent_model: str = "claude-opus-4-5-20251101"
    interview_agent_model: str = "claude-opus-4-5-20251101"
    boss_agent_model: str = "claude-opus-4-5-20251101"
    job_scout_model: str = "gpt-4.1"                         # GPT-4.1 (OpenAI)
    embedding_model: str = "text-embedding-3-small"           # Supabase pgvector

    # Optional explicit provider overrides per agent.
    # Leave None to let the router infer from model name (recommended).
    company_agent_provider: Optional[str] = Field(None, env="COMPANY_AGENT_PROVIDER")
    rizwan_agent_provider: Optional[str] = Field(None, env="RIZWAN_AGENT_PROVIDER")
    interview_agent_provider: Optional[str] = Field(None, env="INTERVIEW_AGENT_PROVIDER")
    boss_agent_provider: Optional[str] = Field(None, env="BOSS_AGENT_PROVIDER")
    job_scout_provider: Optional[str] = Field(None, env="JOB_SCOUT_PROVIDER")

    # ── Forward-looking model slots for Phase 1+ (G2 resume builder graph) ──
    # These don't replace per-agent models above; they're explicit assignments
    # for the new LangGraph nodes. Override via env when graph lands.
    g2_insider_expert_model: str = Field("gemini-2.5-pro", env="G2_INSIDER_EXPERT_MODEL")
    g2_advocate_model: str = Field("claude-opus-4-5-20251101", env="G2_ADVOCATE_MODEL")
    g2_meta_critic_model: str = Field("gemini-2.5-pro", env="G2_META_CRITIC_MODEL")
    g2_writer_model: str = Field("claude-opus-4-5-20251101", env="G2_WRITER_MODEL")
    g2_ats_critic_a_model: str = Field("deepseek-reasoner", env="G2_ATS_CRITIC_A_MODEL")
    g2_ats_critic_b_model: str = Field("kimi-k2.5", env="G2_ATS_CRITIC_B_MODEL")
    g2_orchestrator_model: str = Field("claude-opus-4-5-20251101", env="G2_ORCHESTRATOR_MODEL")
    g2_polisher_model: str = Field("claude-opus-4-5-20251101", env="G2_POLISHER_MODEL")

    # G2 graph control
    g2_max_iterations: int = 3              # Writer ↔ Critic loops
    g2_target_ats_score: int = 95           # Polisher gate
    g2_meta_critic_lookback: int = 5        # Past resumes to read for THIS company

    # Phase 1.11: per-build cost cap.
    #   Hard cap that forces the orchestrator to converge early if a single
    #   resume build's cumulative LLM spend exceeds this. Designed for
    #   "production safety" — the worst-case cost when iterations run away
    #   should still be bounded.
    #   Override per-build via POST /jobs/{id}/generate-resume?max_cost_usd=X.
    g2_max_cost_usd: float = Field(5.0, env="G2_MAX_COST_USD")

    # Phase 1.12: persona quality gate.
    #   Refuses to invoke G2 for a company whose persona quality is below
    #   this threshold. Three levels: 'high' (0 unknown sections), 'medium'
    #   (1-2 unknown), 'low' (3+ unknown). Default 'medium' blocks builds
    #   for low-quality personas (Visa, Thunes, Wio Bank, Payoneer,
    #   Merchant Acquiring …) — saves ~$5 per blocked build that would
    #   produce a poor resume due to insufficient recruitment intel.
    #   Override per-build via POST /jobs/{id}/generate-resume?force=true.
    g2_min_persona_quality: str = Field("medium", env="G2_MIN_PERSONA_QUALITY")

    # ── G3 Interview Prep Graph (Phase 2) ─────────────────────────────────
    # Multi-LLM graph that builds a persona-aware interview prep pack per
    # application/round. 7 logical nodes / 9 actual functions across 5
    # providers. Triggered manually from the dashboard once the user moves
    # the application to interview status.
    #
    # See docs/G3_INTERVIEW_PREP_GRAPH.md for the full design.
    g3_behavioral_predictor_model: str = Field(
        "claude-opus-4-5-20251101", env="G3_BEHAVIORAL_PREDICTOR_MODEL"
    )
    g3_technical_predictor_model: str = Field(
        "gemini-2.5-pro", env="G3_TECHNICAL_PREDICTOR_MODEL"
    )
    g3_domain_predictor_model: str = Field(
        "claude-opus-4-5-20251101", env="G3_DOMAIN_PREDICTOR_MODEL"
    )
    g3_star_matcher_model: str = Field(
        "claude-opus-4-5-20251101", env="G3_STAR_MATCHER_MODEL"
    )
    g3_mock_interviewer_model: str = Field(
        "claude-opus-4-5-20251101", env="G3_MOCK_INTERVIEWER_MODEL"
    )
    g3_mock_critic_model: str = Field(
        "deepseek-reasoner", env="G3_MOCK_CRITIC_MODEL"
    )

    # G3 graph control
    g3_max_iterations: int = 2              # Mock interviewer ↔ critic loops
    g3_target_answer_score: int = 80        # Mock answer convergence gate

    # Phase 2: per-prep cost cap (mirrors G2 Phase 1.11 design).
    #   Hard cap that forces the mock_interview_loop to terminate early if
    #   cumulative LLM spend exceeds this. Designed for "production safety"
    #   — the worst-case cost when iterations run away should still be bounded.
    #   Override per-prep via POST /jobs/{id}/prep-interview?max_cost_usd=X.
    g3_max_cost_usd: float = Field(3.0, env="G3_MAX_COST_USD")

    # Phase 2: persona quality gate (reuses resume_agents.g2_run.check_persona_quality_gate).
    #   Refuses to invoke G3 for a company whose persona quality is below
    #   this threshold. Same three tiers as G2's gate. Default 'medium'
    #   blocks the same low-quality personas (Visa, Thunes, ...). The gate
    #   logic is in resume_agents.g2_run — we just pass this slot as
    #   min_quality= to that function.
    g3_min_persona_quality: str = Field("medium", env="G3_MIN_PERSONA_QUALITY")

    # Phase 1.10: cost alerts (daily check + weekly digest).
    #   Daily check fires after the boss audit at 22:00 GST. Compares
    #   today's cumulative spend (from agent_call_log) against
    #   daily_cost_alert_usd; if exceeded, dispatches via Slack webhook
    #   (preferred — faster) or SendGrid email (fallback).
    #   Weekly digest fires Sundays 09:00 GST: provider conversion rates,
    #   top spenders, error rate trends, recent cost-capped builds.
    daily_cost_alert_usd: float = Field(20.0, env="DAILY_COST_ALERT_USD")
    weekly_cost_digest: bool    = Field(True, env="WEEKLY_COST_DIGEST")
    slack_webhook_url: Optional[str] = Field(None, env="SLACK_WEBHOOK_URL")
    alert_email_to: Optional[str]    = Field(None, env="ALERT_EMAIL_TO")
    daily_alert_time: str            = Field("22:00", env="DAILY_ALERT_TIME")
    weekly_digest_time: str          = Field("09:00", env="WEEKLY_DIGEST_TIME")

    # ── Supabase ────────────────────────────────────────────────────────────
    supabase_url: str = Field(..., env="SUPABASE_URL")
    supabase_service_key: str = Field(..., env="SUPABASE_SERVICE_KEY")
    supabase_anon_key: Optional[str] = Field(None, env="SUPABASE_ANON_KEY")

    # ── Search ──────────────────────────────────────────────────────────────
    serper_api_key: Optional[str] = Field(None, env="SERPER_API_KEY")
    # Phase 2.2 — Apify deep-research for persona building.
    # Get a token at https://console.apify.com/settings/integrations.
    # Free tier: $5/month credit, enough for ~25 company persona builds.
    apify_token: Optional[str] = Field(None, env="APIFY_TOKEN")
    serper_endpoint: str = "https://google.serper.dev/search"

    # ── Email ───────────────────────────────────────────────────────────────
    sendgrid_api_key: Optional[str] = Field(None, env="SENDGRID_API_KEY")
    digest_email_to: str = Field("rizwanzaffar.pk@gmail.com", env="DIGEST_EMAIL_TO")
    digest_email_from: str = Field("noreply@jobhunt.local", env="DIGEST_EMAIL_FROM")

    # ── API Server ──────────────────────────────────────────────────────────
    port: int = Field(8000, env="PORT")
    environment: str = Field("development", env="ENVIRONMENT")
    secret_key: str = Field("change-me-in-production", env="SECRET_KEY")

    # ── Scheduling ──────────────────────────────────────────────────────────
    job_scout_time: str = Field("09:00", env="JOB_SCOUT_TIME")
    boss_agent_time: str = Field("21:00", env="BOSS_AGENT_TIME")
    timezone: str = Field("Asia/Dubai", env="TIMEZONE")

    # ── Thresholds ──────────────────────────────────────────────────────────
    fit_score_threshold: int = 40       # Only process jobs >= this score
    company_freshness_hours: int = 24   # Boss Agent re-scrapes if older than this
    max_jobs_per_run: int = 20          # Max jobs processed per daily run
    max_company_dialogue_turns: int = 6 # Max back-and-forth between agents

    # ── Paths ────────────────────────────────────────────────────────────────
    profile_path: str = "config/profile.yml"
    resume_md_path: str = "cv.md"
    output_resumes_dir: str = "output/resumes"
    output_reports_dir: str = "output/reports"
    output_interview_dir: str = "output/interview_prep"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


# Singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
