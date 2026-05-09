'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { triggerPersonaSynthesis } from '@/lib/profile-api'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'
import { LiveRegion } from '@/components/ui/LiveRegion'

interface Props {
  /**
   * Empty string = run synthesizer over ALL personas (or, if qualityFilter
   * is set, just personas at that quality tier).
   *
   * Mutually exclusive with `qualityFilter`: pass a real company name OR
   * a qualityFilter, never both.
   */
  companyName: string
  size?: 'sm' | 'md'
  /** Override the button label (defaults: "Regenerate" / "Regenerate all") */
  label?: string
  /** Force re-synthesis even if no new data since last run */
  showForce?: boolean
  /**
   * Phase 1.13: bulk-regenerate only personas at this quality tier.
   */
  qualityFilter?: 'low' | 'medium' | 'high' | 'unknown'
}

export default function RegeneratePersonaButton({
  companyName,
  size = 'sm',
  label,
  showForce = false,
  qualityFilter,
}: Props) {
  const router = useRouter()
  const [running, setRunning] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [pending, startTransition] = useTransition()

  async function go(force: boolean) {
    setRunning(true)
    setMsg(null)
    try {
      await triggerPersonaSynthesis({
        company_name: companyName,
        force,
        quality_filter: qualityFilter,
      })
      setMsg('Started — refresh in ~30 s')
      setTimeout(() => {
        startTransition(() => router.refresh())
      }, 25_000)
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : 'Failed')
    } finally {
      setRunning(false)
      setTimeout(() => setMsg(null), 30_000)
    }
  }

  const defaultLabel = qualityFilter
    ? `Regenerate ${qualityFilter}`
    : companyName
    ? 'Regenerate'
    : 'Regenerate all'

  const titleText = qualityFilter
    ? `Re-synthesize all '${qualityFilter}'-quality personas in one shot (skips any with no new data)`
    : companyName
    ? 'Re-synthesize this persona from outcomes + transcripts (skips if no new data)'
    : 'Re-synthesize ALL personas (companies with no new data are skipped automatically)'

  return (
    <span className="inline-flex items-center gap-2">
      <Button
        variant="secondary"
        size={size === 'md' ? 'sm' : 'xs'}
        onClick={() => go(false)}
        loading={running}
        title={titleText}
      >
        <Icon name="refresh" size={size === 'md' ? 12 : 10} />
        {label || defaultLabel}
      </Button>
      {showForce && (
        <Button
          variant="ghost"
          size={size === 'md' ? 'sm' : 'xs'}
          onClick={() => go(true)}
          loading={running}
          title="Force re-synthesis even if no new data — useful after editing the seed prompt"
        >
          force
        </Button>
      )}
      <LiveRegion>
        {msg && (
          <span className="text-2xs text-fg-muted">
            {msg}{pending && ' (refreshing…)'}
          </span>
        )}
      </LiveRegion>
    </span>
  )
}
