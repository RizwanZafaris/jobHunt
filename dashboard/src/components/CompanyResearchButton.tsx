'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { triggerCompanyResearch } from '@/lib/profile-api'

interface Props {
  companyName?: string
  priority?: string
  label?: string
  variant?: 'primary' | 'secondary'
}

export default function CompanyResearchButton({ companyName, priority, label, variant = 'primary' }: Props) {
  const router = useRouter()
  const [running, setRunning] = useState(false)
  const [msg, setMsg] = useState('')

  async function trigger() {
    setRunning(true)
    setMsg('Triggering CompanyAgent...')
    try {
      await triggerCompanyResearch({ company_name: companyName, priority, force: false })
      setMsg('✅ Research started in background — refreshes in ~60-90s')
      setTimeout(() => {
        router.refresh()
        setMsg('')
      }, 90000)
    } catch (e: any) {
      setMsg(`❌ ${e.message}`)
    } finally {
      setRunning(false)
    }
  }

  const cls =
    variant === 'primary'
      ? 'bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white'
      : 'bg-gray-800 hover:bg-gray-700 disabled:opacity-50 text-gray-200 border border-gray-700'

  return (
    <div className="flex items-center gap-2">
      <button
        onClick={trigger}
        disabled={running}
        className={`text-xs ${cls} px-3 py-1.5 rounded-lg font-medium whitespace-nowrap`}
      >
        {running ? '⏳ Running...' : label || (companyName ? '🔬 Research' : '🔬 Research All')}
      </button>
      {msg && <span className="text-xs text-gray-400">{msg}</span>}
    </div>
  )
}
