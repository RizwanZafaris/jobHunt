/**
 * PeopleDiscoveryButton — fires the Perplexity-based people finder.
 *
 * Replaces LinkedInImportButton (2026-05-27). Instead of asking the user
 * to export their LinkedIn connections as a CSV, this button triggers
 * the server-side PeopleFinderAgent which queries Perplexity Sonar per
 * target company for Recruiters + Heads of Talent + Hiring Managers +
 * Senior PMs in target geos.
 *
 * Runtime: 60-120s for 20 companies. We surface a busy spinner and
 * disable the button while the request is in flight.
 */
'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'

export interface DiscoveryResult {
  ok: boolean
  companies_processed: number
  total_queries: number
  total_people_persisted: number
  message?: string
}

export interface PeopleDiscoveryButtonProps {
  onResult: (result: DiscoveryResult) => void
  /** Cap on companies per run. Default 20. */
  limit?: number
  /** Override the button label for empty-state CTAs. */
  label?: string
}

export function PeopleDiscoveryButton({
  onResult,
  limit = 20,
  label = 'Discover people',
}: PeopleDiscoveryButtonProps) {
  const [busy, setBusy] = useState(false)
  const [errMsg, setErrMsg] = useState<string | null>(null)

  async function run() {
    setBusy(true)
    setErrMsg(null)
    try {
      const res = await fetch(
        `/api/proxy/actions/today/trigger-people-discovery?limit_companies=${limit}`,
        { method: 'POST' },
      )
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        throw new Error(body.detail || `Request failed (${res.status})`)
      }
      const result: DiscoveryResult = await res.json()
      onResult(result)
    } catch (err) {
      setErrMsg(err instanceof Error ? err.message : 'Discovery failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex flex-col items-stretch sm:items-end gap-1">
      <Button
        type="button"
        variant="secondary"
        size="md"
        onClick={run}
        loading={busy}
      >
        <Icon name="search" size={14} />
        {busy ? 'Discovering…' : label}
      </Button>
      {busy && (
        <p className="text-2xs text-fg-subtle">
          Querying Perplexity across {limit} target companies — 1-2 min.
        </p>
      )}
      {errMsg && (
        <p role="alert" className="text-2xs text-danger">
          {errMsg}
        </p>
      )}
    </div>
  )
}

export default PeopleDiscoveryButton
