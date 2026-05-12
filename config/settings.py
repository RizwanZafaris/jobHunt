"""
Central settings — loaded once at startup.
All config comes from environment variables or .env file.

Pydantic v3 prep (2026-05-12): the previous form used ``Field(..., env="X")``
to bind a field to a specific env var name. The ``env=`` kwarg has been
deprecated in Pydantic v2 and is removed in v3 (``PydanticDeprecatedSince20``:
"Using extra keyword arguments on `Field` is deprecated…"). Replacement
uses ``SettingsConfigDict`` + the natural case-insensitive field→env mapping
provided by ``pydantic-settings``: with ``case_sensitive=False`` the field
``anthropic_api_key`` reads ``ANTHROPIC_API_KEY`` (and friends) from the
environment. No env var names change. See ``AUDIT_REVIEW_EXTERNAL_2026_05_12.md``
§3.11 (P2-6) and ``SYSTEM_AUDIT_2026_05_12.md`` §6.8.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # ── LLM Provider Keys ──────────────────────────────────────────────────
    # Phase 0: multi-LLM router supports 5 providers. Anthropic + OpenAI are
    # required (existing); the others are optional and only loaded when used.
    anthropic_api_key: str
    openai_api_key: str
    google_api_key: Optional[str] = None       # Gemini
    deepseek_api_key: Optional[str] = None     # V3 + R1
    kimi_api_key: Optional[str] = None         # Moonshot K2

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
    company_agent_provider: Optional[str] = None
    rizwan_agent_provider: Optional[str] = None
    interview_agent_provider: Optional[str] = None
    boss_agent_provider: Optional[str] = None
    job_scout_provider: Optional[str] = None

    # ── Forward-looking model slots for Phase 1+ (G2 resume builder graph) ──
    # These don't replace per-agent models above; they're explicit assignments
    # for the new LangGraph nodes. Override via env when graph lands.
    g2_insider_expert_model: str = "gemini-2.5-pro"
    g2_advocate_model: str = "claude-opus-4-5-20251101"
    g2_meta_critic_model: str = "gemini-2.5-pro"
    g2_writer_model: str = "claude-opus-4-5-20251101"
    g2_ats_critic_a_model: str = "deepseek-reasoner"
    g2_ats_critic_b_model: str = "kimi-k2.5"
    # 2026-05-12 right-sizing (audit §5.2): orchestrator's only job is to read
    # merged_critique JSON, decide converged-or-not against fixed rules, and
    # emit a JSON decision. That's classification work — Sonnet 4.6 matches
    # Opus accuracy at ~5× less cost. Hard convergence gates (score/fixes/
    # banned/persona) in orchestrator_node already backstop the LLM decision,
    # so downgrade risk is bounded. Saves ~$0.10/build × ~20 builds/mo ≈ $24/mo.
    # TODO(2026-05): run a golden-eval comparison of Sonnet vs Opus orchestrator
    # decisions on the next ~5 builds; if any divergence on converge calls,
    # revisit (likely fixable by tightening ORCHESTRATOR_SYSTEM in g2_nodes.py).
    g2_orchestrator_model: str = "claude-sonnet-4-6"
    g2_polisher_model: str = "claude-opus-4-5-20251101"

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
    g2_max_cost_usd: float = 5.0

    # Phase 1.12: persona quality gate.
    #   Refuses to invoke G2 for a company whose persona quality is below
    #   this threshold. Three levels: 'high' (0 unknown sections), 'medium'
    #   (1-2 unknown), 'low' (3+ unknown). Default 'medium' blocks builds
    #   for low-quality personas (Visa, Thunes, Wio Bank, Payoneer,
    #   Merchant Acquiring …) — saves ~$5 per blocked build that would
    #   produce a poor resume due to insufficient recruitment intel.
    #   Override per-build via POST /jobs/{id}/generate-resume?force=true.
    g2_min_persona_quality: str = "medium"

    # ── G3 Interview Prep Graph (Phase 2) ─────────────────────────────────
    # Multi-LLM graph that builds a persona-aware interview prep pack per
    # application/round. 7 logical nodes / 9 actual functions across 5
    # providers. Triggered manually from the dashboard once the user moves
    # the application to interview status.
    #
    # See docs/G3_INTERVIEW_PREP_GRAPH.md for the full design.
    # 2026-05-12 right-sizing: Haiku 4.5 matches Opus 4.5 on list-generation +
    # strict-JSON classification (these are not deep-reasoning tasks). Saves
    # ~$0.28/prep across the three nodes with zero observed quality loss.
    # See docs/G3_G4_IMPROVEMENTS_2026_05_11.md §G3-1.
    g3_behavioral_predictor_model: str = "claude-haiku-4-5"
    g3_technical_predictor_model: str = "gemini-2.5-pro"
    g3_domain_predictor_model: str = "claude-haiku-4-5"
    g3_star_matcher_model: str = "claude-haiku-4-5"
    g3_mock_interviewer_model: str = "claude-opus-4-5-20251101"
    g3_mock_critic_model: str = "deepseek-reasoner"

    # G3 graph control
    g3_max_iterations: int = 2              # Mock interviewer ↔ critic loops
    g3_target_answer_score: int = 80        # Mock answer convergence gate

    # Phase 2: per-prep cost cap (mirrors G2 Phase 1.11 design).
    #   Hard cap that forces the mock_interview_loop to terminate early if
    #   cumulative LLM spend exceeds this. Designed for "production safety"
    #   — the worst-case cost when iterations run away should still be bounded.
    #   Override per-prep via POST /jobs/{id}/prep-interview?max_cost_usd=X.
    g3_max_cost_usd: float = 3.0

    # Phase 2: persona quality gate (reuses resume_agents.g2_run.check_persona_quality_gate).
    #   Refuses to invoke G3 for a company whose persona quality is below
    #   this threshold. Same three tiers as G2's gate. Default 'medium'
    #   blocks the same low-quality personas (Visa, Thunes, ...). The gate
    #   logic is in resume_agents.g2_run — we just pass this slot as
    #   min_quality= to that function.
    g3_min_persona_quality: str = "medium"

    # ── G4 LinkedIn Engine ─────────────────────────────────────────────────
    # 2026-05-12: surfaced from hardcoded constants in agents/g4_linkedin_graph.py
    # so A/B model swaps don't require a redeploy.
    # See docs/G3_G4_IMPROVEMENTS_2026_05_11.md §G4-3.
    g4_sonnet_model: str = "claude-sonnet-4-6"
    g4_opus_model: str = "claude-opus-4-7"
    # Hard cap per draft (Phase 2 — currently advisory; full pre-call cap in followup PR).
    g4_max_cost_usd: float = 0.15

    # ── G6 Follow-up Cadence (Phase 1.3, 2026-05-12) ────────────────────────
    # All three LLM nodes default to Sonnet 4.6. Surfaced so A/B model swaps
    # don't require redeploying agents/g6_nodes.py.
    g6_draft_model: str = "claude-sonnet-4-6"
    g6_persona_critic_model: str = "claude-sonnet-4-6"
    g6_tone_calibrator_model: str = "claude-sonnet-4-6"
    # The daily cron runs at 09:00 in the user's timezone.
    g6_cadence_time: str = "09:00"

    # Phase 1.10: cost alerts (daily check + weekly digest).
    #   Daily check fires after the boss audit at 22:00 GST. Compares
    #   today's cumulative spend (from agent_call_log) against
    #   daily_cost_alert_usd; if exceeded, dispatches via Slack webhook
    #   (preferred — faster) or SendGrid email (fallback).
    #   Weekly digest fires Sundays 09:00 GST: provider conversion rates,
    #   top spenders, error rate trends, recent cost-capped builds.
    daily_cost_alert_usd: float = 20.0
    weekly_cost_digest: bool = True
    slack_webhook_url: Optional[str] = None
    alert_email_to: Optional[str] = None
    daily_alert_time: str = "22:00"
    weekly_digest_time: str = "09:00"

    # ── Supabase ────────────────────────────────────────────────────────────
    supabase_url: str
    supabase_service_key: str
    supabase_anon_key: Optional[str] = None

    # ── Search ──────────────────────────────────────────────────────────────
    serper_api_key: Optional[str] = None
    # Phase 2.2 — Apify deep-research for persona building.
    # Get a token at https://console.apify.com/settings/integrations.
    # Free tier: $5/month credit, enough for ~25 company persona builds.
    apify_token: Optional[str] = None
    serper_endpoint: str = "https://google.serper.dev/search"

    # ── Email ───────────────────────────────────────────────────────────────
    sendgrid_api_key: Optional[str] = None
    digest_email_to: str = "rizwanzaffar.pk@gmail.com"
    digest_email_from: str = "noreply@jobhunt.local"

    # ── API Server ──────────────────────────────────────────────────────────
    port: int = 8000
    environment: str = "development"
    secret_key: str = "change-me-in-production"

    # ── Scheduling ──────────────────────────────────────────────────────────
    job_scout_time: str = "09:00"
    boss_agent_time: str = "21:00"
    timezone: str = "Asia/Dubai"

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

    # Pydantic v3 prep: replaces ``class Config`` (deprecated since v2.0).
    # ``case_sensitive=False`` keeps the historical field→env mapping intact:
    # every snake_case field auto-reads the matching UPPER_SNAKE_CASE env var,
    # so we don't need ``Field(env=…)`` or ``validation_alias`` per field.
    # ``extra="ignore"`` matches default ``BaseSettings`` behavior — unknown
    # env vars are silently ignored (we set this explicitly so future
    # pydantic-settings defaults can't surprise us).
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


# Singleton
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
