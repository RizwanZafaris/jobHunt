/**
 * Supabase browser client (Phase 4, P4-2).
 *
 * Companion to the read-only server client in ./server.ts. This one runs in the
 * browser ('use client' components) and owns the interactive auth surface:
 * Google OAuth sign-in, sign-out, and reading the live session client-side.
 *
 * Gated by the same flag as everything else: when NEXT_PUBLIC_SINGLE_USER_MODE
 * is on (default), the app never calls these — the dashboard behaves exactly as
 * today. Returns null if Supabase env vars are missing so callers degrade
 * cleanly instead of crashing a partially-configured deploy.
 *
 * Env:
 *   NEXT_PUBLIC_SUPABASE_URL       — Supabase project URL
 *   NEXT_PUBLIC_SUPABASE_ANON_KEY  — Supabase anon/publishable key
 */
'use client'

import { createBrowserClient } from '@supabase/ssr'
import type { SupabaseClient } from '@supabase/supabase-js'

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''

export function isMultiTenantMode(): boolean {
  return process.env.NEXT_PUBLIC_SINGLE_USER_MODE === '0'
}

let _client: SupabaseClient | null = null

/**
 * Process-wide singleton browser client (cookie-based session storage via
 * @supabase/ssr). Null when env isn't configured.
 */
export function getSupabaseBrowserClient(): SupabaseClient | null {
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return null
  if (_client) return _client
  _client = createBrowserClient(SUPABASE_URL, SUPABASE_ANON_KEY)
  return _client
}
