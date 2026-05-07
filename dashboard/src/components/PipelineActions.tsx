'use client'

import { useState } from 'react'
import { triggerPipeline, triggerBossAudit } from '@/lib/api'

export default function PipelineActions() {
  const [loading, setLoading] = useState<string | null>(null)
  const [message, setMessage] = useState('')

  const handleRun = async (type: 'pipeline' | 'boss') => {
    setLoading(type)
    setMessage('')
    try {
      if (type === 'pipeline') {
        await triggerPipeline()
        setMessage('✅ Pipeline started in background')
      } else {
        await triggerBossAudit()
        setMessage('✅ Boss audit triggered')
      }
      setTimeout(() => setMessage(''), 4000)
    } catch (e) {
      setMessage('❌ Failed — check API connection')
      setTimeout(() => setMessage(''), 4000)
    } finally {
      setLoading(null)
    }
  }

  return (
    <div className="flex items-center gap-2">
      {message && (
        <span className="text-xs text-gray-300 mr-2">{message}</span>
      )}
      <button
        onClick={() => handleRun('pipeline')}
        disabled={!!loading}
        className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
      >
        {loading === 'pipeline' ? '⏳ Running...' : '🚀 Run Pipeline'}
      </button>
      <button
        onClick={() => handleRun('boss')}
        disabled={!!loading}
        className="text-xs bg-gray-700 hover:bg-gray-600 disabled:opacity-50 disabled:cursor-not-allowed text-white px-3 py-1.5 rounded-lg font-medium transition-colors"
      >
        {loading === 'boss' ? '⏳ Running...' : '🌙 Boss Audit'}
      </button>
      <button
        onClick={() => window.location.reload()}
        className="text-xs bg-gray-800 hover:bg-gray-700 text-gray-300 px-3 py-1.5 rounded-lg font-medium transition-colors border border-gray-700"
      >
        ↻ Refresh
      </button>
    </div>
  )
}
