-- 2026_05_16_032_views_security_invoker.sql
-- B5: 12 analytics/observability views were defined with SECURITY DEFINER
-- (Postgres default for views owned by a privileged role). Under multi-tenant
-- mode that means anyone hitting the view sees every tenant's data, because
-- RLS is evaluated as the view *owner*, not the caller.
--
-- Switching to security_invoker=true makes each query evaluate RLS as the
-- caller, so tenant isolation holds. The views' SQL is unchanged.
--
-- Supabase advisor (security category) flagged each of these as ERROR-level
-- `security_definer_view`. After applying this migration the advisor returns
-- zero findings of that type.
--
-- Idempotent: ALTER VIEW ... SET is a no-op if already set.

ALTER VIEW public.v_resume_build_efficiency        SET (security_invoker = true);
ALTER VIEW public.v_cost_per_outcome               SET (security_invoker = true);
ALTER VIEW public.v_agent_call_log_stats           SET (security_invoker = true);
ALTER VIEW public.v_application_funnel_by_grade    SET (security_invoker = true);
ALTER VIEW public.v_agent_call_health              SET (security_invoker = true);
ALTER VIEW public.v_daily_llm_cost                 SET (security_invoker = true);
ALTER VIEW public.v_application_funnel_by_archetype SET (security_invoker = true);
ALTER VIEW public.v_application_funnel_by_company_size SET (security_invoker = true);
ALTER VIEW public.v_application_funnel             SET (security_invoker = true);
ALTER VIEW public.v_company_conversion_funnel      SET (security_invoker = true);
ALTER VIEW public.v_graph_runs                     SET (security_invoker = true);
ALTER VIEW public.v_rejection_signals              SET (security_invoker = true);
