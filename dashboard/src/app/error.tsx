/**
 * app/error.tsx — route-segment error boundary.
 *
 * Next.js renders this Client Component when a Server Component (or its
 * data fetch) throws anywhere under the app. Without it the user gets
 * Next's raw unstyled error screen. We render a branded, recoverable
 * state inside the AppShell using the same EmptyState primitive the rest
 * of the dashboard uses.
 *
 * `reset()` re-renders the segment (re-runs the failed fetch) — the
 * cheapest recovery for a transient backend/network blip.
 */
'use client'

import { useEffect } from 'react'
import Link from 'next/link'
import { AppShell } from '@/components/layout/AppShell'
import { EmptyState } from '@/components/ui/EmptyState'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'

export default function RouteError({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    // Surface to the console (and any wired observability) without leaking
    // the stack to the user.
    console.error('Route error boundary caught:', error)
  }, [error])

  return (
    <AppShell>
      <EmptyState
        icon="alert-triangle"
        tone="warning"
        title="Something went wrong"
        description="This page hit an unexpected error. It's usually a transient backend or network blip — try again, or head back to Today."
        action={
          <div className="flex items-center gap-2">
            <Button variant="primary" onClick={() => reset()}>
              <Icon name="refresh" size={14} /> Try again
            </Button>
            <Link
              href="/today"
              className="inline-flex items-center gap-1 rounded-md border border-border px-3 py-1.5 text-xs font-medium text-fg hover:bg-surface"
            >
              Back to Today
            </Link>
          </div>
        }
      />
      {error?.digest && (
        <p className="mt-3 text-center text-3xs text-fg-subtle">
          Reference: {error.digest}
        </p>
      )}
    </AppShell>
  )
}
