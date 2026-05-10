/**
 * NotifyForm — email-capture for the Network launch list.
 *
 * Posts to /api/notify-network-launch (stub). Until that route exists
 * the submit handler resolves optimistically and shows a thank-you state.
 *
 * TODO: implement /api/notify-network-launch — see _pending_endpoints.md
 */
'use client'

import { FormEvent, useState } from 'react'
import { TextInput } from '@/components/ui/Field'
import { Button } from '@/components/ui/Button'
import { Icon } from '@/components/ui/Icon'

export function NotifyForm() {
  const [email, setEmail] = useState('')
  const [status, setStatus] = useState<'idle' | 'pending' | 'success' | 'error'>('idle')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (!email || status === 'pending') return
    setStatus('pending')
    setErrorMsg(null)
    try {
      // TODO: replace with real endpoint /api/notify-network-launch
      const res = await fetch('/api/notify-network-launch', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ email }),
      })
      // Treat 404/405 (stub not implemented) as success for now so the
      // UI is testable without backend work.
      if (res.ok || res.status === 404 || res.status === 405) {
        setStatus('success')
      } else {
        const text = await res.text().catch(() => '')
        setStatus('error')
        setErrorMsg(text || `Request failed: ${res.status}`)
      }
    } catch {
      // Network errors — assume the stub isn't there yet, optimistic.
      setStatus('success')
    }
  }

  if (status === 'success') {
    return (
      <div
        role="status"
        className="flex items-center gap-2 rounded-md border border-success-border bg-success-bg/60 px-3 py-2.5 text-xs text-success"
      >
        <Icon name="check" size={14} />
        Thanks — we’ll let you know when Network ships.
      </div>
    )
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col sm:flex-row sm:items-end gap-2 max-w-md">
      <div className="flex-1">
        <TextInput
          label="Email"
          srLabel
          type="email"
          required
          autoComplete="email"
          placeholder="you@example.com"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          error={status === 'error' ? errorMsg ?? 'Something went wrong' : undefined}
        />
      </div>
      <Button type="submit" loading={status === 'pending'} variant="primary" size="md">
        Notify me
      </Button>
    </form>
  )
}

export default NotifyForm
