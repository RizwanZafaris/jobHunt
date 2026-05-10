/**
 * NetworkTab — warm-intro paths to THIS specific company.
 *
 * The workspace bundle ships the top-N paths in `warm_intro_paths`. We
 * render them inline with the path-finder UI from /network's
 * NetworkClient (BestIntroRow), and reuse <IntroDraftModal> for the
 * "Draft intro" button.
 *
 * Three empty states:
 *   1. User has zero people on file → "Import LinkedIn CSV" CTA. The
 *      target company isn't relevant yet because there's nothing to
 *      match against.
 *   2. User has people but no target_companies row for this company →
 *      "This company isn't in your target list yet" — link to /targets.
 *   3. Target resolved but no paths → "No warm intros yet — N contacts
 *      on file" with the import button so the user can add more.
 */
'use client'

import { useState } from 'react'
import Link from 'next/link'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Icon } from '@/components/ui/Icon'
import { Pill } from '@/components/ui/Pill'
import { EmptyState } from '@/components/ui/EmptyState'
import { IntroDraftModal } from '@/components/network/IntroDraftModal'
import { LinkedInImportButton } from '@/components/network/LinkedInImportButton'
import { MOCK_INTRO_DRAFT } from '@/lib/mock/network'
import type { ImportSummary, IntroDraft, ReferralPath } from '@/lib/types/network'

const KIND_LABEL: Record<string, string> = {
  me_first_degree: '1°',
  colleague: 'colleague',
  classmate: 'classmate',
  friend: 'friend',
  family: 'family',
  introduced_by: 'intro',
  same_company_overlap: 'overlap',
  referenced_in_outreach: 'outreach',
}

// TODO: replace with the real /network/draft-intro endpoint when it
// lands in api/network.py. Today we use the same stub the /network
// page uses (consistent UX across the two surfaces).
async function stubDraftIntro(_path: ReferralPath): Promise<IntroDraft> {
  await new Promise((r) => setTimeout(r, 600))
  return MOCK_INTRO_DRAFT
}

export interface NetworkTabProps {
  companyName: string
  paths: ReferralPath[]
  networkSize: number
  targetResolved: boolean
  onNetworkRefresh?: () => void
}

export function NetworkTab({
  companyName,
  paths,
  networkSize,
  targetResolved,
  onNetworkRefresh,
}: NetworkTabProps) {
  const [activePath, setActivePath] = useState<ReferralPath | null>(null)
  const [importSummary, setImportSummary] = useState<ImportSummary | null>(null)

  const handleImport = (summary: ImportSummary) => {
    setImportSummary(summary)
    onNetworkRefresh?.()
  }

  // Empty state 1: zero people on file.
  if (networkSize === 0) {
    return (
      <Card padding="md">
        <EmptyState
          icon="users"
          title="Import your LinkedIn CSV to find warm intros"
          description={`Once you upload your connections, we'll find anyone employed at ${companyName} (or one degree away).`}
          action={<LinkedInImportButton onSummary={handleImport} />}
          hint={
            <>
              How: LinkedIn → Settings & Privacy → Data privacy → Get a copy of your data → Connections.
            </>
          }
        />
        {importSummary && (
          <p role="status" className="mt-3 text-2xs text-success text-center">
            Imported {importSummary.imported} contacts · {importSummary.edges_created} edges ·{' '}
            {importSummary.employments_created} employments
          </p>
        )}
      </Card>
    )
  }

  // Empty state 2: target not in the user's target_companies list.
  if (!targetResolved) {
    return (
      <Card padding="md">
        <EmptyState
          icon="target"
          title={`${companyName} isn't in your target list yet`}
          description={`Add ${companyName} to your targets so we can map your network onto it.`}
          action={
            <Link
              href="/targets"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md text-2xs font-semibold bg-accent text-accent-fg hover:bg-accent-hover transition-colors min-h-9"
            >
              Open targets
              <Icon name="arrow-right" size={12} />
            </Link>
          }
          hint={`${networkSize} contacts on file — ready to match once the target is added.`}
        />
      </Card>
    )
  }

  // Empty state 3: target resolved but zero paths.
  if (paths.length === 0) {
    return (
      <Card padding="md">
        <EmptyState
          icon="users"
          title={`No warm intros to ${companyName} yet`}
          description={`We checked your ${networkSize} contacts — no one currently works there, and no two-hop introducer connects you. Either import more contacts or apply cold this round.`}
          action={<LinkedInImportButton onSummary={handleImport} />}
        />
        {importSummary && (
          <p role="status" className="mt-3 text-2xs text-success text-center">
            Imported {importSummary.imported} contacts. Refresh the page to recompute paths.
          </p>
        )}
      </Card>
    )
  }

  // Happy path — render the ranked path list.
  return (
    <>
      <section aria-labelledby="warm-intros-title" className="space-y-3">
        <header className="flex items-baseline justify-between gap-3 flex-wrap">
          <h2 id="warm-intros-title" className="text-base font-semibold text-fg tracking-tight">
            Warm intros to {companyName}
          </h2>
          <p className="text-2xs text-fg-subtle">
            {paths.length} path{paths.length === 1 ? '' : 's'} from your {networkSize}-person network
          </p>
        </header>
        <ol className="flex flex-col gap-2" aria-label="Warm-intro paths">
          {paths.map((p) => (
            <li key={`${p.target_company_id}-${p.target_employee.id}`}>
              <PathRow path={p} onDraft={() => setActivePath(p)} />
            </li>
          ))}
        </ol>
      </section>

      <IntroDraftModal
        open={activePath !== null}
        path={activePath}
        draftIntro={stubDraftIntro}
        onClose={() => setActivePath(null)}
      />
    </>
  )
}

function PathRow({ path, onDraft }: { path: ReferralPath; onDraft: () => void }) {
  const target = path.target_employee
  return (
    <article className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3 hover:border-border-strong transition-colors">
      <span
        aria-hidden
        className="inline-flex items-center justify-center w-9 h-9 rounded-md bg-surface-raised text-fg-muted shrink-0"
      >
        <Icon name="building" size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-semibold text-fg leading-snug truncate">
          {target.full_name}
          {target.current_role && (
            <span className="text-fg-muted font-normal"> · {target.current_role}</span>
          )}
        </p>
        <div className="mt-1 flex items-center gap-1.5 flex-wrap">
          {path.path.map((node, i) => (
            <span key={`${node.id}-${i}`} className="flex items-center gap-1.5">
              <Pill
                tone={i === 0 ? 'accent' : i === path.path.length - 1 ? 'success' : 'neutral'}
                size="xs"
              >
                {node.full_name}
              </Pill>
              {i < path.path.length - 1 && (
                <span className="text-fg-subtle text-2xs">
                  →
                  {path.edge_kinds[i] && (
                    <span className="ml-1">{KIND_LABEL[path.edge_kinds[i]] ?? path.edge_kinds[i]}</span>
                  )}
                </span>
              )}
            </span>
          ))}
        </div>
      </div>
      <div className="text-right shrink-0">
        <p className="text-2xs text-fg-subtle tnum">
          {path.hop_count} hop{path.hop_count === 1 ? '' : 's'} ·{' '}
          {(path.total_strength * 100).toFixed(0)}% trust
        </p>
        <Button variant="primary" size="xs" onClick={onDraft} className="mt-1">
          Draft intro
        </Button>
      </div>
    </article>
  )
}

export default NetworkTab
