import { NextRequest, NextResponse } from 'next/server'

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
const SECRET_KEY = process.env.API_SECRET_KEY || ''

export const dynamic = 'force-dynamic'

async function handler(req: NextRequest, ctx: { params: Promise<{ path: string[] }> }) {
  // Next 15: route handler `params` is async.
  const { path: pathSegments } = await ctx.params
  const path = pathSegments.join('/')
  const search = req.nextUrl.search
  const target = `${API_URL}/${path}${search}`

  const headers: Record<string, string> = {
    'X-Secret-Key': SECRET_KEY,
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
