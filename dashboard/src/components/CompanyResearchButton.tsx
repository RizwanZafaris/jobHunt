'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { triggerCompanyResearch } from '@/lib/profile-api'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'
import { LiveRegion } from '@/components/ui/LiveRegion'

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
    setMsg('Triggering CompanyAgent…')
    try {
      await triggerCompanyResearch({ company_name: companyName, priority, force: false })
      setMsg('Research started — refreshes in ~60–90 s')
      setTimeout(() => {
        router.refresh()
        setMsg('')
      }, 90_000)
    } catch (e: unknown) {
      setMsg(e instanceof Error ? e.message : 'Failed')
    } finally {
      setRunning(false)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Button
        variant={variant === 'primary' ? 'primary' : 'secondary'}
        size="sm"
        onClick={trigger}
        loading={running}
      >
        <Icon name="search" size={12} />
        {label || (companyName ? 'Research' : 'Research all')}
      </Button>
      <LiveRegion>
        {msg && <span className="text-2xs text-fg-muted">{msg}</span>}
      </LiveRegion>
    </div>
  )
}
