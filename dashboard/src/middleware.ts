/**
 * Next.js middleware - Supabase session refresh + route protection (Phase 4, P4-2).
 *
 * WARNING: runs on every matched request. It is therefore gated HARD on the
 * master flag: when NEXT_PUBLIC_SINGLE_USER_MODE is anything other than "0"
 * (the default - unset or "1"), this returns NextResponse.next() on the very
 * first line, so the live single-user dashboard is byte-for-byte unchanged.
 * None of the Supabase logic below executes unless multi-tenant mode is on.
 *
 * In multi-tenant mode (flag === "0") it:
 *   1. Refreshes the Supabase auth session cookie (the official supabase/ssr
 *      pattern - call getUser() so expired access tokens are rotated and the
 *      refreshed cookies are written onto the response).
 *   2. Redirects unauthenticated users to /login for protected routes.
 *   3. Leaves the public auth routes (/login, /signup, /auth/*) open.
 *
 * If Supabase env vars are missing it also no-ops (cannot refresh without them),
 * so a partially-configured preview never hard-fails.
 */
import { NextResponse, type NextRequest } from 'next/server'
import { createServerClient } from '@supabase/ssr'

const SUPABASE_URL = process.env.NEXT_PUBLIC_SUPABASE_URL || ''
const SUPABASE_ANON_KEY = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY || ''

// Routes reachable without a session. Everything else requires auth in
// multi-tenant mode.
const PUBLIC_PREFIXES = ['/login', '/signup', '/auth']

function isPublicPath(pathname: string): boolean {
  return PUBLIC_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(p + '/'),
  )
}

export async function middleware(request: NextRequest) {
  // Hard gate: single-user mode (default) -> do nothing.
  if (process.env.NEXT_PUBLIC_SINGLE_USER_MODE !== '0') {
    return NextResponse.next()
  }
  // Cannot refresh a session without Supabase env - degrade to no-op.
  if (!SUPABASE_URL || !SUPABASE_ANON_KEY) {
    return NextResponse.next()
  }

  // Response we may mutate cookies onto (session refresh writes here).
  let response = NextResponse.next({ request })

  const supabase = createServerClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
    cookies: {
      getAll() {
        return request.cookies.getAll()
      },
      setAll(cookiesToSet) {
        // Write refreshed cookies onto both request (for downstream) and the
        // outgoing response, per the supabase/ssr middleware pattern.
        cookiesToSet.forEach(({ name, value }) =>
          request.cookies.set(name, value),
        )
        response = NextResponse.next({ request })
        cookiesToSet.forEach(({ name, value, options }) =>
          response.cookies.set(name, value, options),
        )
      },
    },
  })

  // IMPORTANT: getUser() (not getSession()) - it revalidates the token with
  // Supabase and triggers the refresh + setAll() above.
  const {
    data: { user },
  } = await supabase.auth.getUser()

  const { pathname } = request.nextUrl

  // Unauthenticated + protected route -> bounce to /login (preserve intended
  // destination so we can return there post-login).
  if (!user && !isPublicPath(pathname)) {
    const url = request.nextUrl.clone()
    url.pathname = '/login'
    url.searchParams.set('next', pathname)
    return NextResponse.redirect(url)
  }

  // Authenticated user hitting /login or /signup -> send to the app.
  if (user && (pathname === '/login' || pathname === '/signup')) {
    const url = request.nextUrl.clone()
    url.pathname = '/today'
    url.search = ''
    return NextResponse.redirect(url)
  }

  return response
}

export const config = {
  // Run on app routes but skip Next internals, static assets, the API proxy
  // (it does its own auth forwarding), and common file extensions.
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|api/|.*\\.(?:svg|png|jpg|jpeg|gif|webp|ico|css|js|woff2?)$).*)',
  ],
}
