import Link from 'next/link'
import { cookies } from 'next/headers'
import { redirect } from 'next/navigation'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { Icon } from '@/components/ui/Icon'

export const dynamic = 'force-dynamic'

export const metadata = {
  title: 'Admin · Job Hunt',
  description: 'Admin tools — only visible to admins.',
}

// Stub auth: until real auth lands, the user email is sourced from a
// `user_email` cookie (or falls back to the local-dev default in
// CLAUDE.md). Replace with a real `getServerSession()` once auth ships.
const ADMIN_ALLOWLIST: ReadonlyArray<string> = ['rizwanzaffar.pk@gmail.com']

async function getCurrentUserEmail(): Promise<string | null> {
  // Next 15: cookies() is async.
  const fromCookie = (await cookies()).get('user_email')?.value
  if (fromCookie) return fromCookie
  // Local-dev convenience — matches CLAUDE.md's userEmail.
  if (process.env.NODE_ENV !== 'production') {
    return 'rizwanzaffar.pk@gmail.com'
  }
  return null
}

export default async function AdminPage() {
  const email = await getCurrentUserEmail()
  // TODO: surface a flash message via cookie or query param ('?flash=admin_only')
  // once we have a toast component. For now /today shows nothing extra.
  if (!email || !ADMIN_ALLOWLIST.includes(email)) {
    redirect('/today?flash=admin_only')
  }

  return (
    <AppShell>
      <PageHeader
        eyebrow="Admin"
        title="Admin tools"
        description="Only visible to admins."
      />

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <Link
          href="/admin/users"
          className="group rounded-lg border border-border bg-surface p-4 hover:border-border-strong shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors"
        >
          <div className="flex items-start gap-3">
            <div className="inline-flex items-center justify-center w-9 h-9 rounded-md bg-surface-raised text-fg-muted">
              <Icon name="users" size={16} />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-fg group-hover:text-accent transition-colors">
                Users
              </h2>
              <p className="text-2xs text-fg-muted mt-1 leading-relaxed">
                Manage allowlist, roles, and account state. (Stub.)
              </p>
            </div>
            <Icon name="arrow-up-right" size={14} className="text-fg-subtle" />
          </div>
        </Link>

        <Link
          href="/admin/eval-reports"
          className="group rounded-lg border border-border bg-surface p-4 hover:border-border-strong shadow-sm focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors"
        >
          <div className="flex items-start gap-3">
            <div className="inline-flex items-center justify-center w-9 h-9 rounded-md bg-surface-raised text-fg-muted">
              <Icon name="document" size={16} />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="text-sm font-semibold text-fg group-hover:text-accent transition-colors">
                Eval reports
              </h2>
              <p className="text-2xs text-fg-muted mt-1 leading-relaxed">
                Browse agent eval runs and regression diffs. (Stub.)
              </p>
            </div>
            <Icon name="arrow-up-right" size={14} className="text-fg-subtle" />
          </div>
        </Link>
      </div>

      <Card tone="muted" padding="sm">
        <p className="text-2xs text-fg-subtle">
          Signed in as <code className="font-mono text-fg">{email}</code>
        </p>
      </Card>
    </AppShell>
  )
}
