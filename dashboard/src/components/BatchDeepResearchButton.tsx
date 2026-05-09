/**
 * BatchDeepResearchButton — kick off Apify+Gemini deep research across
 * all targets that don't have a persona yet.
 *
 * Backend: POST /personas/deep-research-batch?priority=&only_missing=true
 * Each company takes ~2 min; the batch runs sequentially in the
 * background so this button just queues the work and returns.
 */
'use client'

import { useState } from 'react'
import { Button } from '@/components/ui/Button'
import { LiveRegion } from '@/components/ui/LiveRegion'

interface Props {
  /** When true, only build for companies missing a persona. */
  onlyMissing?: boolean
  /** Limit by priority tier. Omit for all. */
  priority?: 'high' | 'medium' | 'low'
  size?: 'sm' | 'md'
  label?: string
}

export default function BatchDeepResearchButton({
  onlyMissing = true,
  priority,
  size = 'md',
  label,
}: Props) {
  const [running, setRunning] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  const buttonLabel = label ?? (
    onlyMissing
      ? `Deep-research missing${priority ? ` (${priority})` : ''}`
      : `Deep-research all${priority ? ` (${priority})` : ''}`
  )

  async function go() {
    if (
      !confirm(
        `This will fire Apify + Gemini for every queued company. Per company ~$0.20 + ~2 min. Continue?`,
      )
    ) {
      return
    }
    setRunning(true)
    setMsg(null)
    try {
      const params = new URLSearchParams({ only_missing: String(onlyMissing) })
      if (priority) params.set('priority', priority)
      const res = await fetch(
        `/api/proxy/personas/deep-research-batch?${params.toString()}`,
        { method: 'POST' },
      )
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`HTTP ${res.status}: ${body.slice(0, 200)}`)
      }
      const data = await res.json()
      setMsg(
        `Queued ${data.queued_count} companies. Estimated ~${data.queued_count * 2} min total.`,
      )
    } catch (err) {
      const e = err instanceof Error ? err.message : String(err)
      setMsg(`Failed: ${e}`)
    } finally {
      setRunning(false)
    }
  }

  return (
    <>
      <Button
        type="button"
        variant="secondary"
        size={size}
        onClick={go}
        disabled={running}
        title="Live web research via Apify + Gemini long-context, sequential, ~2 min/company"
      >
        {running ? 'Queueing…' : buttonLabel}
      </Button>
      <LiveRegion>{msg}</LiveRegion>
    </>
  )
}
