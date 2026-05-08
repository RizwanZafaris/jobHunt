'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import {
  generateResumeForJob,
  PersonaQualityGateError,
} from '@/lib/profile-api'

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
    setMsg(opts?.force ? 'Force-building (low quality persona)...' : 'Head of Recruitment Agency analyzing...')
    setGateConfirm(null)
    try {
      const r = await generateResumeForJob(jobId, opts)
      setMsg(`✅ ${r.message || 'Started'}`)
      setTimeout(() => {
        router.refresh()
        setMsg('')
      }, 60000)
    } catch (e: any) {
      // Phase 1.12: persona quality gate — show confirm dialog instead of error
      if (e instanceof PersonaQualityGateError) {
        setGateConfirm(e)
        setMsg('')
      } else {
        setMsg(`❌ ${e.message}`)
        setTimeout(() => setMsg(''), 6000)
      }
    } finally {
      setRunning(false)
    }
  }

  if (score < 85) {
    return (
      <div className="text-xs text-gray-500 italic">
        Resume generation gated at 85+. This job scored {score}/100.
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-1.5 items-end">
      <button
        onClick={() => trigger()}
        disabled={running}
        className={`text-xs whitespace-nowrap px-4 py-2 rounded-lg font-medium ${
          alreadyGenerated
            ? 'bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700'
            : 'bg-emerald-600 hover:bg-emerald-500 text-white'
        } disabled:opacity-50`}
      >
        {running
          ? '⏳ Generating...'
          : alreadyGenerated
          ? '🔁 Re-generate Resume'
          : '🎯 Generate Tailored Resume'}
      </button>

      {archetype && (
        <span className="text-[10px] text-gray-500 text-right">
          targeting <span className="text-emerald-400">{archetype}</span> archetype
        </span>
      )}
      {msg && <span className="text-xs text-gray-400 text-right">{msg}</span>}

      {/* Phase 1.12 confirm dialog when persona quality blocked */}
      {gateConfirm && (
        <div className="mt-2 max-w-sm bg-amber-900/30 border border-amber-700/60 rounded-lg p-3 text-left">
          <p className="text-xs font-semibold text-amber-200 mb-1">
            ⚠ Low-quality persona for {gateConfirm.detail.company_name}
          </p>
          <p className="text-[11px] text-amber-100/80 leading-relaxed mb-2">
            Persona quality is{' '}
            <span className="font-bold">{gateConfirm.detail.persona_quality}</span>{' '}
            (v{gateConfirm.detail.persona_version}
            {gateConfirm.detail.unknown_sections != null
              ? `, ${gateConfirm.detail.unknown_sections}/5 sections sparse`
              : ''}
            ). Building anyway may waste tokens on a poor result.
          </p>
          <details className="text-[10px] text-amber-100/60 mb-2">
            <summary className="cursor-pointer">Why blocked?</summary>
            <p className="mt-1">{gateConfirm.detail.message}</p>
          </details>
          <div className="flex gap-2">
            <button
              onClick={() => trigger({ force: true })}
              disabled={running}
              className="text-[11px] bg-amber-600 hover:bg-amber-500 text-white px-2 py-1 rounded font-medium"
            >
              Force build anyway
            </button>
            <button
              onClick={() => setGateConfirm(null)}
              disabled={running}
              className="text-[11px] bg-gray-700 hover:bg-gray-600 text-gray-200 px-2 py-1 rounded"
            >
              Cancel
            </button>
          </div>
          <p className="text-[10px] text-amber-100/50 mt-2">
            Tip: regenerate the persona on{' '}
            <a href="/personas" className="underline">/personas</a>{' '}
            after logging more outcomes.
          </p>
        </div>
      )}
    </div>
  )
}
