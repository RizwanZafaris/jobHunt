/**
 * app/not-found.tsx — branded 404.
 *
 * Renders for unmatched routes (and explicit notFound() calls). Without
 * it the user gets Next's bare default. We keep them inside the AppShell
 * with a clear path back to Today.
 */
import Link from 'next/link'
import { AppShell } from '@/components/layout/AppShell'
import { EmptyState } from '@/components/ui/EmptyState'

export default function NotFound() {
  return (
    <AppShell>
      <EmptyState
        icon="search"
        title="Page not found"
        description="That page doesn't exist or may have moved. Let's get you back on track."
        action={
          <Link
            href="/today"
            className="inline-flex items-center gap-1 rounded-md bg-accent px-3 py-1.5 text-xs font-semibold text-accent-fg hover:bg-accent-hover"
          >
            Back to Today
          </Link>
        }
      />
    </AppShell>
  )
}
