'use client'

import { useState } from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import {
  generateResumeForJob,
  PersonaQualityGateError,
} from '@/lib/profile-api'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'
import { LiveRegion } from '@/components/ui/LiveRegion'
import { Card } from '@/components/ui/Card'

interface Props {
  jobId: number
  score: number
  alreadyGenerated: boolean
  archetype?: string | null
}

export default function GenerateResumeButton({ jobId, score, alreadyGenerated, archetype }: Props) {
  const router = useRouter()
  const [running, setRunning] = useState(false)
  const [msg, setMsg] = useState('')
  const [gateConfirm, setGateConfirm] = useState<PersonaQualityGateError | null>(null)

  async function trigger(opts?: { force?: boolean }) {
    setRunning(true)
    setMsg(opts?.force ? 'Force-building (low-quality persona)…' : 'Recruitment expert analyzing…')
    setGateConfirm(null)
    try {
      const r = await generateResumeForJob(jobId, opts)
      setMsg(r.message || 'Started')
      setTimeout(() => {
        router.refresh()
        setMsg('')
      }, 60_000)
    } catch (e: unknown) {
      if (e instanceof PersonaQualityGateError) {
        setGateConfirm(e)
        setMsg('')
      } else {
        setMsg(e instanceof Error ? e.message : 'Failed')
        setTimeout(() => setMsg(''), 6000)
      }
    } finally {
      setRunning(false)
    }
  }

  if (score < 85) {
    return (
      <div className="text-2xs text-fg-subtle italic">
        Resume generation gated at 85+. This job scored <span className="font-mono">{score}</span>/100.
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-2 items-end">
      <Button
        variant={alreadyGenerated ? 'secondary' : 'primary'}
        size="md"
        onClick={() => trigger()}
        loading={running}
      >
        <Icon name="sparkles" size={14} />
        {alreadyGenerated ? 'Re-generate resume' : 'Generate tailored resume'}
      </Button>

      {archetype && (
        <span className="text-2xs text-fg-subtle text-right">
          targeting <span className="text-success">{archetype}</span> archetype
        </span>
      )}
      <LiveRegion>
        {msg && <span className="text-2xs text-fg-muted text-right">{msg}</span>}
      </LiveRegion>

      {gateConfirm && (
        <Card tone="warning" padding="sm" className="mt-1 max-w-sm">
          <p className="text-xs font-semibold text-warning mb-1">
            Low-quality persona for {gateConfirm.detail.company_name}
          </p>
          <p className="text-2xs text-fg-muted leading-relaxed mb-2">
            Persona quality is{' '}
            <span className="font-semibold text-fg">{gateConfirm.detail.persona_quality}</span>{' '}
            (v{gateConfirm.detail.persona_version}
            {gateConfirm.detail.unknown_sections != null
              ? `, ${gateConfirm.detail.unknown_sections}/5 sections sparse`
              : ''}
            ). Building anyway may waste tokens on a poor result.
          </p>
          <details className="text-2xs text-fg-subtle mb-2">
            <summary className="cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded">
              Why blocked?
            </summary>
            <p className="mt-1">{gateConfirm.detail.message}</p>
          </details>
          <div className="flex gap-2">
            <Button variant="warning" size="sm" onClick={() => trigger({ force: true })} loading={running}>
              Force build anyway
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setGateConfirm(null)} disabled={running}>
              Cancel
            </Button>
          </div>
          <p className="text-2xs text-fg-subtle mt-2">
            Tip: regenerate the persona on{' '}
            <Link href="/personas" className="text-info underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded">
              /personas
            </Link>{' '}
            after logging more outcomes.
          </p>
        </Card>
      )}
    </div>
  )
}
