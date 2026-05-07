"""
Central settings — loaded once at startup.
All config comes from environment variables or .env file.
"""

from pydantic_settings import BaseSettings
from pydantic import Field
from typing import Optional


class Settings(BaseSettings):
    # ── AI Models ──────────────────────────────────────────────────────────
    anthropic_api_key: str = Field(..., env="ANTHROPIC_API_KEY")
    openai_api_key: str = Field(..., env="OPENAI_API_KEY")

    # Agent model assignments
    # BUG-01 fix: correct Anthropic model strings (claude-opus-4-5 was invalid)
    company_agent_model: str = "claude-opus-4-5-20251101"    # Claude Opus 4.5
    rizwan_agent_model: str = "claude-opus-4-5-20251101"
    interview_agent_model: str = "claude-opus-4-5-20251101"
    boss_agent_model: str = "claude-opus-4-5-20251101"
    job_scout_model: str = "gpt-4.1"                         # GPT-4.1 (OpenAI)
    embedding_model: str = "text-embedding-3-small"           # Supabase pgvector

    # ── Supabase ────────────────────────────────────────────────────────────
    supabase_url: str = Field(..., env="SUPABASE_URL")
    supabase_service_key: str = Field(..., env="SUPABASE_SERVICE_KEY")
    supabase_anon_key: Optional[str] = Field(None, env="SUPABASE_ANON_KEY")

    # ── Search ──────────────────────────────────────────────────────────────
    serper_api_key: Optional[str] = Field(None, env="SERPER_API_KEY")
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
