import { NextRequest, NextResponse } from 'next/server'
import { getAccessToken, isMultiTenantMode } from '@/lib/supabase/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const SECRET_KEY = process.env.API_SECRET_KEY || ''

export const dynamic = 'force-dynamic'

async function handler(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  // Next 15: route handler `params` is async.
  const { path: pathSegments } = await ctx.params
  const path = pathSegments.join('/')
  const search = req.nextUrl.search
  const target = `${API_URL}/${path}${search}`

  // Auth forwarding (Phase 1, P1-7):
  //   - SINGLE_USER mode (default): attach the shared X-Secret-Key, exactly as
  //     before. Byte-for-byte unchanged when NEXT_PUBLIC_SINGLE_USER_MODE is
  //     unset or "1".
  //   - Multi-tenant mode (NEXT_PUBLIC_SINGLE_USER_MODE=0): read the Supabase
  //     session server-side and forward Authorization: Bearer <access_token>.
  //     If there's no session/token, fall back to X-Secret-Key so internal/
  //     unauthenticated calls still reach the backend (which will 401 the
  //     per-user endpoints appropriately).
  const headers: Record<string, string> = {}
  let authForwarded = false
  if (isMultiTenantMode()) {
    const token = await getAccessToken()
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
      authForwarded = true
    }
  }
  if (!authForwarded) {
    headers['X-Secret-Key'] = SECRET_KEY
  }
  const contentType = req.headers.get('content-type')
  if (contentType) headers['Content-Type'] = contentType

  const init: RequestInit = { method: req.method, headers, cache: 'no-store' }
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    init.body = await req.text()
  }

  try {
    const upstream = await fetch(target, init)
    const body = await upstream.text()
    return new NextResponse(body, {
      status: upstream.status,
      headers: { 'Content-Type': upstream.headers.get('content-type') || 'application/json' },
    })
  } catch (e: any) {
    return NextResponse.json(
      { error: 'proxy_failed', detail: String(e?.message || e) },
      { status: 502 }
    )
  }
}

export { handler as GET, handler as POST, handler as PUT, handler as DELETE, handler as PATCH }
