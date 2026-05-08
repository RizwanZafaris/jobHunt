'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { generateResumeForJob } from '@/lib/profile-api'

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

  async function trigger() {
    setRunning(true)
    setMsg('Head of Recruitment Agency analyzing...')
    try {
      const r = await generateResumeForJob(jobId)
      setMsg(`✅ ${r.message || 'Started'}`)
      setTimeout(() => {
        router.refresh()
        setMsg('')
      }, 60000)
    } catch (e: any) {
      setMsg(`❌ ${e.message}`)
      setTimeout(() => setMsg(''), 6000)
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
    <div className="flex flex-col gap-1.5">
      <button
        onClick={trigger}
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
    </div>
  )
}
