'use client'

import { useState } from 'react'
import { triggerPipeline, triggerBossAudit } from '@/lib/api'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'
import { LiveRegion } from '@/components/ui/LiveRegion'

export default function PipelineActions() {
  const [loading, setLoading] = useState<string | null>(null)
  const [message, setMessage] = useState('')
  const [tone, setTone] = useState<'success' | 'danger' | null>(null)

  const handleRun = async (type: 'pipeline' | 'boss') => {
    setLoading(type)
    setMessage('')
    setTone(null)
    try {
      if (type === 'pipeline') {
        await triggerPipeline()
        setMessage('Pipeline started in background')
      } else {
        await triggerBossAudit()
        setMessage('Boss audit triggered')
      }
      setTone('success')
      setTimeout(() => setMessage(''), 4000)
    } catch {
      setMessage('Failed — check API connection')
      setTone('danger')
      setTimeout(() => setMessage(''), 4000)
    } finally {
      setLoading(null)
    }
  }

  return (
    <div className="flex items-center gap-1.5">
      <LiveRegion>
        {message && (
          <span
            className={`text-2xs mr-1 ${tone === 'danger' ? 'text-danger' : 'text-success'}`}
          >
            {message}
          </span>
        )}
      </LiveRegion>
      <Button
        size="sm"
        variant="primary"
        onClick={() => handleRun('pipeline')}
        loading={loading === 'pipeline'}
        disabled={!!loading}
      >
        <Icon name="play" size={12} />
        Run pipeline
      </Button>
      <Button
        size="sm"
        variant="secondary"
        onClick={() => handleRun('boss')}
        loading={loading === 'boss'}
        disabled={!!loading}
      >
        <Icon name="moon" size={12} />
        <span className="hidden sm:inline">Boss audit</span>
      </Button>
      <Button
        size="sm"
        variant="ghost"
        onClick={() => window.location.reload()}
        aria-label="Refresh page"
        title="Refresh"
      >
        <Icon name="refresh" size={14} />
      </Button>
    </div>
  )
}
