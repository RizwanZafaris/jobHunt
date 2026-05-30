/**
 * /auth/callback — OAuth code → session exchange (Phase 4, P4-2).
 *
 * Supabase redirects here after Google auth with a `?code=...`. We exchange it
 * for a session (writing the auth cookies onto the redirect response) and send
 * the user on to `next` (default /today; new users come in with next=/onboarding).
 *
 * Server route handler using the @supabase/ssr cookie adapter. If env is missing
 * or the exchange fails, we redirect to /login with an error flag rather than
 * 500.
 */
import { NextResponse, type NextRequest } from 'next/server'
import { createServerClient } from '@supabase/ssr'

export const dynamic = 'force-dynamic'

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''

export async function GET(request: NextRequest) {
  const { searchParams, origin } = request.nextUrl
  const code = searchParams.get('code')
  const next = searchParams.get('next') || '/today'

  function loginRedirect(reason: string) {
    const url = new URL('/login', origin)
    url.searchParams.set('error', reason)
    return NextResponse.redirect(url)
  }

  if (!code) return loginRedirect('missing_code')
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) return loginRedirect('not_configured')

  // Redirect response we attach the session cookies to.
  const response = NextResponse.redirect(new URL(next, origin))

  const supabase = createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return request.cookies.getAll()
      },
      setAll(cookiesToSet) {
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        )
      },
    },
  })

  const { error } = await supabase.auth.exchangeCodeForSession(code)
  if (error) return loginRedirect('exchange_failed')

  return response
}
