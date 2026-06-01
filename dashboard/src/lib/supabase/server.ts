/**
 * Minimal Supabase server client (Phase 1, P1-7).
 *
 * Reads the Supabase auth session from request cookies so server-side code
 * (the API proxy, RSC fetchers) can obtain the user's access token and forward
 * it to the backend as `Authorization: Bearer <token>`.
 *
 * This is deliberately minimal: it does NOT include login/signup UI or the
 * session-refresh middleware (that is Phase 4). It only needs to *read* an
 * existing session. When `NEXT_PUBLIC_SINGLE_USER_MODE` is on (the default),
 * none of this runs — the proxy keeps using `X-Secret-Key` unchanged.
 *
 * Env:
 *   NEXT_PUBLIC_SUPABASE_URL       — Supabase project URL
 *   NEXT_PUBLIC_SUPABASE_ANON_KEY  — Supabase anon/publishable key
 */
import { cookies } from 'next/headers'
import { createServerClient } from '@supabase/ssr'

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''

/**
 * True when single-user mode is OFF, i.e. we should resolve a real user JWT.
 * Defaults to single-user ON (returns false) so the system behaves as today
 * unless the flag is explicitly disabled.
 */
export function isMultiTenantMode(): boolean {
  return process.env.NEXT_PUBLIC_SINGLE_USER_MODE === '0'
}

/**
 * Build a read-only Supabase server client bound to the current request's
 * cookies. Returns null if Supabase env vars aren't configured (so callers can
 * cleanly fall back rather than crash a partially-configured deploy).
 *
 * `cookies()` is async in Next 15. We only read cookies here; writes are no-ops
 * because route handlers / RSC can't always set cookies and we don't need to
 * refresh the session in this PR (Phase 4 handles refresh in middleware).
 */
export async function createSupabaseServerClient() {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return null

  const cookieStore = await cookies()

  return createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return cookieStore.getAll()
      },
      // No-op: this PR only reads the session. Phase 4's middleware owns
      // session refresh + cookie writes.
      setAll() {
        /* intentionally empty */
      },
    },
  })
}

/**
 * Resolve the current user's Supabase access token (JWT) from request cookies,
 * or null when there is no session / multi-tenant mode is off / env is missing.
 *
 * Used by the API proxy to forward `Authorization: Bearer <token>` upstream.
 */
export async function getAccessToken(): Promise<string | null> {
  if (!isMultiTenantMode()) return null
  try {
    const supabase = await createSupabaseServerClient()
    if (!supabase) return null
    const {
      data: { session },
    } = await supabase.auth.getSession()
    return session?.access_token ?? null
  } catch {
    // Never let an auth-read failure take down the proxy; fall back to no token.
    return null
  }
}
