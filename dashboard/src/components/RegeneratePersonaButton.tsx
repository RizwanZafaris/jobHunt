'use client'

import { useState, useTransition } from 'react'
import { useRouter } from 'next/navigation'
import { triggerPersonaSynthesis } from '@/lib/profile-api'

interface Props {
  /** Empty string = run synthesizer over ALL personas */
  companyName: string
  size?: 'sm' | 'md'
  /** Override the button label (defaults: "Regenerate" / "Regenerate All") */
  label?: string
  /** Force re-synthesis even if no new data since last run */
  showForce?: boolean
}

export default function RegeneratePersonaButton({
  companyName,
  size = 'sm',
  label,
  showForce = false,
}: Props) {
  const router = useRouter()
  const [running, setRunning] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)
  const [pending, startTransition] = useTransition()

  async function go(force: boolean) {
    setRunning(true)
    setMsg(null)
    try {
      await triggerPersonaSynthesis({ company_name: companyName, force })
      setMsg('✅ Started — refresh in ~30s')
      // Auto-refresh SSR data after a short delay so the user sees the new
      // last_synthesized_at when the synth completes
      setTimeout(() => {
        startTransition(() => router.refresh())
      }, 25_000)
    } catch (e: any) {
      setMsg(`❌ ${e.message || 'Failed'}`)
    } finally {
      setRunning(false)
      setTimeout(() => setMsg(null), 30_000)
    }
  }

  const cls =
    size === 'md'
      ? 'text-xs bg-violet-700 hover:bg-violet-600 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg font-medium'
      : 'text-[11px] bg-gray-800 hover:bg-violet-800 border border-gray-700 hover:border-violet-700 disabled:opacity-50 text-gray-300 hover:text-violet-200 px-2 py-0.5 rounded'

  return (
    <span className="inline-flex items-center gap-1.5">
      <button
        onClick={() => go(false)}
        disabled={running}
        className={cls}
        title={
          companyName
            ? 'Re-synthesize this persona from outcomes + transcripts (skips if no new data)'
            : 'Re-synthesize ALL personas (companies with no new data are skipped automatically)'
        }
      >
        {running ? '⏳' : '↻'} {label || (companyName ? 'Regenerate' : 'Regenerate All')}
      </button>
      {showForce && (
        <button
          onClick={() => go(true)}
          disabled={running}
          className={cls}
          title="Force re-synthesis even if no new data — useful after editing the seed prompt"
        >
          force
        </button>
      )}
      {msg && (
        <span className="text-[10px] text-gray-400">
          {msg}
          {pending && ' (refreshing…)'}
        </span>
      )}
    </span>
  )
}
