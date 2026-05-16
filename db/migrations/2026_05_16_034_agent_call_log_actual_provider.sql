-- 2026_05_16_034_agent_call_log_actual_provider.sql
-- B-OR1: Add agent_call_log.actual_provider column for OpenRouter routing
-- attribution. When the hardening layer falls back to OpenRouter, the
-- column `provider` stores 'openrouter' (the routing layer), and
-- `actual_provider` stores the underlying provider that served the
-- request (e.g. 'anthropic', 'openai', 'google'). For direct provider
-- calls, actual_provider is NULL (the existing `provider` column already
-- has the true value).
--
-- Without this column, the first OpenRouter call would crash with a
-- PostgREST "column not found" error in the log callback, dropping the
-- entire telemetry row.
--
-- Idempotent: safe to apply twice.

ALTER TABLE public.agent_call_log
  ADD COLUMN IF NOT EXISTS actual_provider TEXT NULL;

COMMENT ON COLUMN public.agent_call_log.actual_provider IS
  'Native provider that served an OpenRouter-routed request. NULL for direct calls. Populated by agents/llm_router.py::_default_log_callback.';

-- Optional index for OR-attribution analytics queries. Partial index
-- only on non-NULL rows so it stays small.
CREATE INDEX IF NOT EXISTS idx_agent_call_log_actual_provider
  ON public.agent_call_log (actual_provider)
  WHERE actual_provider IS NOT NULL;
