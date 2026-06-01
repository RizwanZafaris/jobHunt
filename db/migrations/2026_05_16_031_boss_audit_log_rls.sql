-- 2026_05_16_031_boss_audit_log_rls.sql
-- B4: boss_audit_log had RLS disabled. The Supabase advisor flagged this as
-- an ERROR. The table has no user_id — it's a single-row-per-day global
-- audit trail of the Boss Agent's daily run (refreshed companies, jobs
-- found, digest content). It's deliberately not user-owned, so the right
-- fix is "enable RLS + deny all non-service-role access". Service role
-- (the worker + scheduler + API server) bypasses RLS by default in
-- Supabase, so background writes continue to work.
--
-- Without this fix: any authenticated user in multi-tenant mode could
-- read every system audit entry (low risk but flagged for compliance).
--
-- Idempotent: safe to apply twice.

ALTER TABLE IF EXISTS public.boss_audit_log ENABLE ROW LEVEL SECURITY;

-- Explicit deny: with RLS on and no permissive policies, anon/authenticated
-- roles get nothing. Service role bypasses regardless. This is the
-- intended state for global admin tables.
DROP POLICY IF EXISTS boss_audit_log_deny_all ON public.boss_audit_log;
CREATE POLICY boss_audit_log_deny_all
  ON public.boss_audit_log
  FOR ALL
  TO authenticated, anon
  USING (false)
  WITH CHECK (false);
