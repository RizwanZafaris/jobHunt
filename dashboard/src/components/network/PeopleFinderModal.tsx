/**
 * PeopleFinderModal — discover people at a target company via Apollo and add
 * them to your referral network.
 *
 * Replaces the old LinkedIn CSV upload. Flow:
 *   1. Enter a company (pre-filled when launched from a company workspace) +
 *      optional title filter.
 *   2. POST /apollo/search-people → list of people at that company.
 *   3. "Add to network" on a row → POST /network/people (referral graph).
 *
 * Mirrors IntroDraftModal's dialog shell (role=dialog, Esc-to-close, backdrop)
 * and uses the existing UI kit + semantic tokens. Apollo search is a paid-plan
 * feature; a 402/403 from the backend is surfaced as a clear message.
 */
'use client'

import { useEffect, useRef, useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'

interface FoundPerson {
  name: string
  title: string | null
  linkedin_url: string | null
  organization_name: string | null
}

export interface PeopleFinderModalProps {
  /** Pre-fill + lock the company when launched from a company workspace. */
  company?: string
  onClose: () => void
  /** Called after at least one person is added, so the parent can refresh. */
  onAdded?: () => void
}

// Common seniority filters Apollo accepts (people_seniorities).
const SENIORITIES = [
  { value: '', label: 'Any seniority' },
  { value: 'director', label: 'Director' },
  { value: 'vp', label: 'VP' },
  { value: 'head', label: 'Head' },
  { value: 'c_suite', label: 'C-suite' },
  { value: 'manager', label: 'Manager' },
]

export function PeopleFinderModal({ company, onClose, onAdded }: PeopleFinderModalProps) {
  const [companyQuery, setCompanyQuery] = useState(company ?? '')
  const [title, setTitle] = useState('')
  const [seniority, setSeniority] = useState('')
  const [results, setResults] = useState<FoundPerson[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [added, setAdded] = useState<Record<string, 'adding' | 'done'>>({})
  const inputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  async function search() {
    const q = companyQuery.trim()
    if (!q) {
      setError('Enter a company name to search.')
      return
    }
    setLoading(true)
    setError(null)
    setResults(null)
    try {
      const titles = title.trim() ? [title.trim()] : undefined
      const res = await fetch('/api/proxy/apollo/search-people', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          q_organization_domains_list: [],
          organization_ids: [],
          person_titles: titles,
          person_seniorities: seniority ? [seniority] : undefined,
          // The backend resolves the org from the name when ids are absent;
          // pass it through as a title-ish hint via q_keywords if supported.
          q_organization_name: q,
          per_page: 25,
        }),
      })
      if (res.status === 402 || res.status === 403) {
        setError('People search needs an Apollo paid plan. Add an APOLLO_API_KEY with search access.')
        setResults([])
        return
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        const detail = typeof body?.detail === 'string' ? body.detail : `Search failed (${res.status})`
        throw new Error(detail)
      }
      const data = await res.json()
      // Apollo returns { people: [...] } or { contacts: [...] } depending on plan.
      const raw: unknown[] = data.people ?? data.contacts ?? data.results ?? []
      const mapped: FoundPerson[] = raw.map((p) => {
        const o = p as Record<string, unknown>
        const org = o.organization as Record<string, unknown> | undefined
        return {
          name:
            (o.name as string) ||
            [o.first_name, o.last_name].filter(Boolean).join(' ') ||
            'Unknown',
          title: (o.title as string) ?? null,
          linkedin_url: (o.linkedin_url as string) ?? null,
          organization_name:
            (org?.name as string) ?? (o.organization_name as string) ?? q,
        }
      })
      setResults(mapped)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed.')
      setResults([])
    } finally {
      setLoading(false)
    }
  }

  async function addPerson(p: FoundPerson, key: string) {
    setAdded((m) => ({ ...m, [key]: 'adding' }))
    try {
      const res = await fetch('/api/proxy/network/people', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          full_name: p.name,
          linkedin_url: p.linkedin_url,
          headline: p.title,
          current_company_name: p.organization_name,
          current_role: p.title,
        }),
      })
      if (!res.ok) throw new Error(`Add failed (${res.status})`)
      setAdded((m) => ({ ...m, [key]: 'done' }))
      onAdded?.()
    } catch {
      // Revert so the user can retry.
      setAdded((m) => {
        const next = { ...m }
        delete next[key]
        return next
      })
      setError(`Couldn't add ${p.name}. Try again.`)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-fg/40 backdrop-blur-sm p-4"
      onClick={onClose}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Find people at a company"
        className="w-full max-w-lg rounded-lg border border-border bg-surface shadow-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-border px-4 py-3">
          <h2 className="text-sm font-semibold text-fg">Find people</h2>
          <button onClick={onClose} aria-label="Close" className="text-fg-subtle hover:text-fg">
            <Icon name="x" size={16} />
          </button>
        </div>

        {/* Search inputs */}
        <div className="border-b border-border p-4 flex flex-col gap-2">
          <input
            ref={inputRef}
            value={companyQuery}
            onChange={(e) => setCompanyQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && search()}
            placeholder="Company (e.g. Adyen)"
            disabled={!!company}
            className="w-full rounded-md border border-border bg-bg text-fg text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-accent disabled:opacity-60"
            aria-label="Company name"
          />
          <div className="flex gap-2">
            <input
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && search()}
              placeholder="Title contains (optional)"
              className="flex-1 rounded-md border border-border bg-bg text-fg text-sm px-3 py-2 focus:outline-none focus:ring-2 focus:ring-accent"
              aria-label="Title filter"
            />
            <select
              value={seniority}
              onChange={(e) => setSeniority(e.target.value)}
              className="rounded-md border border-border bg-bg text-fg text-2xs px-2 py-2 focus:outline-none focus:ring-2 focus:ring-accent"
              aria-label="Seniority filter"
            >
              {SENIORITIES.map((s) => (
                <option key={s.value} value={s.value}>{s.label}</option>
              ))}
            </select>
            <Button variant="primary" size="sm" onClick={search} loading={loading}>
              <Icon name="search" size={14} />
              Search
            </Button>
          </div>
        </div>

        {/* Results */}
        <div className="p-4 max-h-[50vh] overflow-y-auto">
          {error && <p role="alert" className="text-2xs text-danger mb-2">{error}</p>}

          {loading && (
            <div className="flex flex-col gap-2">
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-10 w-full animate-pulse rounded bg-surface-raised" />
              ))}
            </div>
          )}

          {!loading && results && results.length === 0 && !error && (
            <div className="text-center py-8">
              <Icon name="users" size={20} className="mx-auto text-fg-subtle" />
              <p className="mt-2 text-xs text-fg-muted">No people found. Try a broader title or check the company name.</p>
            </div>
          )}

          {!loading && results && results.length > 0 && (
            <ul className="flex flex-col divide-y divide-border">
              {results.map((p, i) => {
                const key = `${p.name}-${i}`
                const state = added[key]
                return (
                  <li key={key} className="flex items-center gap-3 py-2">
                    <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-surface-raised text-fg-subtle">
                      <Icon name="users" size={14} />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="text-xs font-medium text-fg truncate">{p.name}</p>
                      <p className="text-2xs text-fg-muted truncate">
                        {p.title || '—'}{p.organization_name ? ` · ${p.organization_name}` : ''}
                      </p>
                    </div>
                    {p.linkedin_url && (
                      <a
                        href={p.linkedin_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="shrink-0 text-fg-subtle hover:text-accent"
                        aria-label={`${p.name} on LinkedIn`}
                      >
                        <Icon name="external-link" size={14} />
                      </a>
                    )}
                    <Button
                      variant={state === 'done' ? 'ghost' : 'secondary'}
                      size="xs"
                      disabled={state === 'adding' || state === 'done'}
                      loading={state === 'adding'}
                      onClick={() => addPerson(p, key)}
                    >
                      {state === 'done' ? (
                        <><Icon name="check" size={12} /> Added</>
                      ) : (
                        <><Icon name="plus" size={12} /> Add</>
                      )}
                    </Button>
                  </li>
                )
              })}
            </ul>
          )}

          {!loading && !results && !error && (
            <p className="text-2xs text-fg-subtle text-center py-8">
              Search a target company to find people and seed warm intros.
            </p>
          )}
        </div>

        <div className="flex items-center justify-end border-t border-border px-4 py-3">
          <Button variant="ghost" size="sm" onClick={onClose}>Done</Button>
        </div>
      </div>
    </div>
  )
}

export default PeopleFinderModal
