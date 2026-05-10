/**
 * /applications/[id]/workspace — the Application Workspace.
 *
 * Phase 2 surface. The /today action card's "Start application process"
 * primary CTA lands here. This is the focused, multi-tab page where the
 * user reviews the role, opens the AI-built resume, browses warm intros,
 * preps for the interview, and finally hits "Mark as applied".
 *
 * Server Component fetches the full workspace bundle in one
 * round-trip (api/workspace.py::GET /workspace/{job_id}). All five tabs
 * receive their slice of the bundle as a prop — no client-side
 * waterfall. Interactivity (chat editor, applied checklist, intro draft
 * modal, etc.) lives inside <WorkspaceClient>.
 */
import { notFound } from 'next/navigation'
import { AppShell } from '@/components/layout/AppShell'
import { WorkspaceClient } from '@/components/workspace/WorkspaceClient'
import { fetchWorkspace } from '@/lib/api/workspace'
import type { Workspace, WorkspaceTabKey } from '@/lib/types/workspace'

export const dynamic = 'force-dynamic'

interface WorkspacePageProps {
  params: { id: string }
  searchParams?: { tab?: string }
}

const VALID_TABS: WorkspaceTabKey[] = ['role', 'resume', 'interview', 'network', 'apply']

function resolveInitialTab(raw?: string): WorkspaceTabKey {
  if (raw && (VALID_TABS as string[]).includes(raw)) {
    return raw as WorkspaceTabKey
  }
  return 'role'
}

export async function generateMetadata({ params }: WorkspacePageProps) {
  return {
    title: `Application · ${params.id} · Job Hunt`,
    description: 'Application workspace — role brief, resume editor, warm intros, interview prep, apply.',
  }
}

export default async function WorkspacePage({ params, searchParams }: WorkspacePageProps) {
  const jobId = Number(params.id)
  if (!Number.isFinite(jobId) || jobId <= 0) {
    notFound()
  }

  let workspace: Workspace | null = null
  let errorMessage: string | null = null
  try {
    workspace = await fetchWorkspace(jobId)
  } catch (err) {
    errorMessage = err instanceof Error ? err.message : String(err)
  }

  if (!workspace && errorMessage?.startsWith('fetchWorkspace failed (404)')) {
    notFound()
  }

  return (
    <AppShell wide>
      {workspace ? (
        <WorkspaceClient
          workspace={workspace}
          initialTab={resolveInitialTab(searchParams?.tab)}
        />
      ) : (
        <div
          role="alert"
          className="rounded-md border border-danger-border bg-danger-bg/60 px-4 py-3 text-xs text-danger leading-relaxed"
        >
          <p className="font-semibold mb-1">Workspace unavailable.</p>
          <p>{errorMessage ?? 'The backend did not respond. Try again in a moment.'}</p>
        </div>
      )}
    </AppShell>
  )
}
