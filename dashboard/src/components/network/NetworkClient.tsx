/**
 * NetworkClient — interactive shell for the /network page.
 *
 * Holds the state for:
 *   - the search query (people list)
 *   - the active intro-draft modal target (a ReferralPath)
 *   - a stub draftIntro action that resolves to the mock today; later
 *     this calls /network/draft-intro (see api/NETWORK.md "Future work")
 *
 * The page (Server Component) provides the initial data. This client
 * component does not fetch; the page passes everything pre-loaded so
 * we keep the client bundle small.
 */
'use client'

import { useMemo, useState } from 'react'
import Link from 'next/link'
import { Card } from '@/components/ui/Card'
import { Button } from '@/components/ui/Button'
import { Pill } from '@/components/ui/Pill'
import { Icon } from '@/components/ui/Icon'
import { TextInput } from '@/components/ui/Field'
import { EmptyState } from '@/components/ui/EmptyState'
import { IntroDraftModal } from '@/components/network/IntroDraftModal'
import { LinkedInImportButton } from '@/components/network/LinkedInImportButton'
import { MOCK_INTRO_DRAFT } from '@/lib/mock/network'
import type {
  ImportSummary,
  IntroDraft,
  Person,
  ReferralPath,
  TargetCoverage,
} from '@/lib/types/network'

// BUG-020: until /network/* endpoints are wired into api/server.py the
// page renders the seeded mock fixture from dashboard/src/lib/mock/network.ts.
// Showing those names with no disclaimer made the page look like it
// reflected a real graph. We detect the seed by name overlap (cheap,
// stable) and surface a warning pill near the warm-intros card so the
// user knows what they're looking at.
const SEED_FIXTURE_NAMES = new Set([
  'Sarah Lin',
  'Bob Patel',
  'Alice Hwang',
  'Raj Mehta',
  'Imani Cole',
  'José Ramírez',
])

function isDemoFixture(people: Person[]): boolean {
  if (people.length === 0) return false
  // Treat as demo if every loaded person is in the seed fixture set, OR
  // if the total roster is suspiciously small (≤8) and contains seed
  // names — real LinkedIn imports return hundreds of rows.
  const seedHits = people.filter((p) => SEED_FIXTURE_NAMES.has(p.full_name)).length
  return seedHits >= 3 && people.length <= 12
}

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

export interface NetworkClientProps {
  topPaths: ReferralPath[]
  coverage: TargetCoverage[]
  people: Person[]
}

// TODO: replace with POST /network/draft-intro once that endpoint ships.
// Today the mock returns after a 600ms delay so the loading state is visible.
async function stubDraftIntro(_path: ReferralPath): Promise<IntroDraft> {
  await new Promise((r) => setTimeout(r, 600))
  return MOCK_INTRO_DRAFT
}

export function NetworkClient({ topPaths, coverage, people }: NetworkClientProps) {
  const [search, setSearch] = useState('')
  const [activePath, setActivePath] = useState<ReferralPath | null>(null)
  const [importSummary, setImportSummary] = useState<ImportSummary | null>(null)
  // BUG-020 — disclaim that the loaded names are demo data. Hidden once
  // the user imports a real CSV (importSummary truthy).
  const showDemoBanner = importSummary === null && isDemoFixture(people)

  const filteredPeople = useMemo(() => {
    if (!search.trim()) return people
    const q = search.trim().toLowerCase()
    return people.filter(
      (p) =>
        p.full_name.toLowerCase().includes(q) ||
        (p.headline ?? '').toLowerCase().includes(q) ||
        (p.current_company ?? '').toLowerCase().includes(q),
    )
  }, [people, search])

  const peopleByCompany = useMemo(() => groupByCurrentCompany(filteredPeople), [
    filteredPeople,
  ])

  return (
    <>
      {/* Search + Import row */}
      <Card padding="md">
        <div className="flex flex-col sm:flex-row sm:items-end gap-3">
          <div className="flex-1">
            <TextInput
              label="Search your network"
              srLabel
              type="search"
              placeholder="Search people by name, company, or headline"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
            />
          </div>
          <LinkedInImportButton onSummary={setImportSummary} />
        </div>
        {importSummary && (
          <p
            role="status"
            className="mt-3 text-2xs text-success"
          >
            Imported {importSummary.imported} contacts ·{' '}
            {importSummary.edges_created} edges ·{' '}
            {importSummary.employments_created} employments
            {importSummary.skipped ? ` · skipped ${importSummary.skipped}` : ''}
            {importSummary.errors.length > 0 ? ` · ${importSummary.errors.length} errors` : ''}
          </p>
        )}
      </Card>

      {/* Best warm intros */}
      <section aria-labelledby="best-intros-title" className="space-y-3">
        <header className="flex items-baseline justify-between gap-3 flex-wrap">
          <h2
            id="best-intros-title"
            className="text-base font-semibold text-fg tracking-tight"
          >
            Best warm intros
          </h2>
          <p className="text-2xs text-fg-subtle">
            Top {topPaths.length} of {coverage.length} target companies
          </p>
        </header>
        {showDemoBanner && (
          <Pill tone="warning" size="xs">
            Demo data — import LinkedIn CSV to replace
          </Pill>
        )}
        {topPaths.length === 0 ? (
          <EmptyState
            icon="users"
            title="No paths yet"
            description="Import your LinkedIn connections to seed the graph."
          />
        ) : (
          <ol className="flex flex-col gap-2" aria-label="Top warm-intro paths">
            {topPaths.map((p) => (
              <li key={`${p.target_company_id}-${p.target_employee.id}`}>
                <BestIntroRow path={p} onDraft={() => setActivePath(p)} />
              </li>
            ))}
          </ol>
        )}
      </section>

      {/* Coverage */}
      <section aria-labelledby="coverage-title" className="space-y-3">
        <header className="flex items-baseline justify-between gap-3 flex-wrap">
          <h2 id="coverage-title" className="text-base font-semibold text-fg tracking-tight">
            Target coverage
          </h2>
          <Link
            href="/companies"
            className="text-2xs font-medium text-fg-muted hover:text-fg underline-offset-2 hover:underline"
          >
            Manage targets
          </Link>
        </header>
        <Card padding="none">
          <table className="w-full text-xs" role="table">
            <caption className="sr-only">Target coverage by hop distance</caption>
            <thead className="border-b border-border bg-surface-raised">
              <tr className="text-left">
                <th className="px-4 py-2 font-medium text-fg-muted">Company</th>
                <th className="px-4 py-2 font-medium text-fg-muted">1-hop</th>
                <th className="px-4 py-2 font-medium text-fg-muted">2-hop</th>
                <th className="px-4 py-2 font-medium text-fg-muted">Top path</th>
              </tr>
            </thead>
            <tbody>
              {coverage.map((c) => (
                <tr key={c.target_company_id} className="border-b border-border last:border-b-0">
                  <td className="px-4 py-2.5 font-medium text-fg">{c.name ?? '—'}</td>
                  <td className="px-4 py-2.5 tnum text-fg-muted">{c.paths_count_1hop}</td>
                  <td className="px-4 py-2.5 tnum text-fg-muted">{c.paths_count_2hop}</td>
                  <td className="px-4 py-2.5">
                    {c.top_path ? (
                      <button
                        type="button"
                        onClick={() => setActivePath(c.top_path!)}
                        className="text-fg-muted hover:text-fg underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
                      >
                        Draft via {c.top_path.target_employee.full_name}
                      </button>
                    ) : (
                      <span className="text-fg-subtle">no path</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      </section>

      {/* People grouped by current company */}
      <section aria-labelledby="people-title" className="space-y-3">
        <header className="flex items-baseline justify-between gap-3 flex-wrap">
          <h2 id="people-title" className="text-base font-semibold text-fg tracking-tight">
            Your network
          </h2>
          <p className="text-2xs text-fg-subtle">
            {filteredPeople.length} {filteredPeople.length === 1 ? 'person' : 'people'}
          </p>
        </header>
        {peopleByCompany.length === 0 ? (
          <EmptyState
            icon="search"
            title="No matches"
            description="Try a different name, company, or headline."
          />
        ) : (
          <div className="space-y-3">
            {peopleByCompany.map(({ company, members }) => (
              <Card key={company} padding="none">
                <div className="px-4 py-2.5 border-b border-border bg-surface-raised flex items-center justify-between">
                  <p className="text-2xs font-medium uppercase tracking-wider text-fg-muted">
                    {company}
                  </p>
                  <p className="text-2xs text-fg-subtle tnum">{members.length}</p>
                </div>
                <ul className="divide-y divide-border" aria-label={`Network at ${company}`}>
                  {members.map((p) => (
                    <li key={p.id} className="px-4 py-2.5 flex items-center gap-3">
                      <div className="inline-flex items-center justify-center w-8 h-8 rounded-full bg-surface-raised text-fg-muted shrink-0">
                        <Icon name="users" size={14} />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="text-xs font-medium text-fg truncate">{p.full_name}</p>
                        {p.headline && (
                          <p className="text-2xs text-fg-muted truncate">{p.headline}</p>
                        )}
                      </div>
                      {p.linkedin_url && (
                        <a
                          href={p.linkedin_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-2xs text-fg-muted hover:text-fg flex items-center gap-1 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded px-2 py-1"
                        >
                          LinkedIn
                          <Icon name="arrow-up-right" size={10} />
                        </a>
                      )}
                    </li>
                  ))}
                </ul>
              </Card>
            ))}
          </div>
        )}
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

// ── Sub-components ─────────────────────────────────────────────────────────

function BestIntroRow({ path, onDraft }: { path: ReferralPath; onDraft: () => void }) {
  const target = path.target_employee
  const targetCompany = path.target_company_name ?? target.current_company ?? '—'
  return (
    <article className="flex items-center gap-3 rounded-lg border border-border bg-surface px-4 py-3 hover:border-border-strong transition-colors">
      <span
        aria-hidden
        className="inline-flex items-center justify-center w-9 h-9 rounded-md bg-surface-raised text-fg-muted shrink-0"
      >
        <Icon name="building" size={16} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-2xs uppercase tracking-wider text-fg-subtle font-medium">
          {targetCompany}
        </p>
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

function groupByCurrentCompany(people: Person[]): { company: string; members: Person[] }[] {
  const groups = new Map<string, Person[]>()
  for (const p of people) {
    const key = p.current_company || 'Unaffiliated'
    const existing = groups.get(key)
    if (existing) {
      existing.push(p)
    } else {
      groups.set(key, [p])
    }
  }
  return Array.from(groups.entries())
    .map(([company, members]) => ({
      company,
      members: members.sort((a, b) => a.full_name.localeCompare(b.full_name)),
    }))
    .sort((a, b) => {
      // Unaffiliated last; otherwise larger groups first.
      if (a.company === 'Unaffiliated') return 1
      if (b.company === 'Unaffiliated') return -1
      if (b.members.length !== a.members.length) return b.members.length - a.members.length
      return a.company.localeCompare(b.company)
    })
}

export default NetworkClient
