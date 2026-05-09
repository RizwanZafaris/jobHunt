/**
 * DeepResearchPersonaButton — kick off Apify-powered persona research.
 *
 * One click → /personas/deep-research backend endpoint:
 *   1. Fires 8 parallel Apify rag-web-browser queries about the company
 *   2. Gemini 2.5 Pro long-context synthesis into 13 knowledge sections
 *      + ATS keyword bank + system prompt
 *   3. Upserts company_knowledge + company_personas (bumps version)
 *
 * Cost: ~$0.20/company. Latency: ~2 min. Backgrounded by default —
 * the button shows "started" then refreshes the page after a delay
 * so the new version + quality tier appear.
 */
'use client'

import { useState } from 'react'
import { useRouter } from 'next/navigation'
import { Button } from '@/components/ui/Button'
import { LiveRegion } from '@/components/ui/LiveRegion'

interface Props {
  companyName: string
  size?: 'xs' | 'sm' | 'md'
  label?: string
}

export default function DeepResearchPersonaButton({
  companyName,
  size = 'sm',
  label = 'Deep research',
}: Props) {
  const router = useRouter()
  const [running, setRunning] = useState(false)
  const [msg, setMsg] = useState<string | null>(null)

  async function go() {
    if (!companyName) {
      setMsg('No company specified.')
      return
    }
    setRunning(true)
    setMsg(null)
    try {
      const res = await fetch(
        `/api/proxy/personas/deep-research?company=${encodeURIComponent(companyName)}`,
        { method: 'POST' },
      )
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`HTTP ${res.status}: ${body.slice(0, 200)}`)
      }
      const data = await res.json()
      setMsg(
        `Started: ${data.message || 'persona build queued.'} Refreshing in ~2 min…`,
      )
      // Refresh the route after a delay so the new persona row + quality
      // tier appear when the user is still looking at the page.
      setTimeout(() => {
        router.refresh()
        setRunning(false)
      }, 130_000)
    } catch (err) {
      const e = err instanceof Error ? err.message : String(err)
      setMsg(`Failed: ${e}`)
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
        aria-label={`Deep-research persona for ${companyName}`}
        title="Live web research via Apify + Gemini long-context (~2 min, ~$0.20)"
      >
        {running ? 'Researching…' : label}
      </Button>
      <LiveRegion>{msg}</LiveRegion>
    </>
  )
}
