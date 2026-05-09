/**
 * BossChat — interactive chat with the Boss agent.
 *
 * Threaded conversation grounded in live pipeline state. Each turn
 * POSTs the full message history to /boss/chat (via the Next.js
 * proxy). Cost / latency / model surfaced under each assistant turn
 * so the user can sanity-check spend.
 *
 * a11y:
 *   - Real <form> with submit; Enter sends, Shift+Enter newline
 *   - aria-live region announces new assistant turns to screen readers
 *   - Focus returns to the textarea after each response
 *   - Touch targets ≥ 44px on mobile
 */
'use client'

import { FormEvent, KeyboardEvent, useEffect, useRef, useState, useId } from 'react'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Pill } from '@/components/ui/Pill'
import { LiveRegion } from '@/components/ui/LiveRegion'

type Role = 'user' | 'assistant'

interface ChatMessage {
  role: Role
  content: string
  cost_usd?: number
  latency_ms?: number
  model?: string
}

const SUGGESTIONS = [
  "What's the highest-leverage thing I should do this week?",
  'Which targets should I drop and why?',
  'How is my cost per build trending?',
  'Walk me through my pipeline state.',
]

const TEXTAREA_BASE =
  'w-full bg-surface text-fg placeholder:text-fg-subtle border border-border-strong rounded-md px-3 py-2 text-xs ' +
  'focus-visible:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30 ' +
  'disabled:opacity-50 transition-colors resize-none'

export default function BossChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [announce, setAnnounce] = useState('')

  const inputId = useId()
  const hintId = useId()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const transcriptRef = useRef<HTMLDivElement>(null)

  // Auto-scroll to latest message
  useEffect(() => {
    if (transcriptRef.current) {
      transcriptRef.current.scrollTop = transcriptRef.current.scrollHeight
    }
  }, [messages.length, pending])

  // Refocus the input after each response lands
  useEffect(() => {
    if (!pending) {
      textareaRef.current?.focus()
    }
  }, [pending])

  async function send(content: string) {
    const trimmed = content.trim()
    if (!trimmed || pending) return

    const next: ChatMessage[] = [...messages, { role: 'user', content: trimmed }]
    setMessages(next)
    setInput('')
    setPending(true)
    setError(null)

    try {
      const res = await fetch('/api/proxy/boss/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: next.map(({ role, content }) => ({ role, content })),
        }),
      })
      if (!res.ok) {
        const body = await res.text()
        throw new Error(`HTTP ${res.status}: ${body.slice(0, 300)}`)
      }
      const data = await res.json()
      const assistant: ChatMessage = {
        role: 'assistant',
        content: String(data.content ?? '').trim() || '(empty response)',
        cost_usd: data.cost_usd,
        latency_ms: data.latency_ms,
        model: data.model,
      }
      setMessages((prev) => [...prev, assistant])
      setAnnounce(`Boss replied with ${assistant.content.slice(0, 120)}`)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      setMessages((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: `(boss is offline — ${msg})`,
        },
      ])
    } finally {
      setPending(false)
    }
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault()
    void send(input)
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      void send(input)
    }
  }

  function clear() {
    setMessages([])
    setInput('')
    setError(null)
  }

  const empty = messages.length === 0

  return (
    <div className="flex flex-col gap-3">
      <Card padding="none" className="overflow-hidden">
        <div
          ref={transcriptRef}
          aria-label="Conversation transcript"
          className="h-[60vh] min-h-[360px] max-h-[700px] overflow-y-auto px-4 py-4 sm:px-6"
        >
          {empty && (
            <div className="text-fg-muted text-sm">
              <p className="mb-3">
                Ask the Boss anything about your pipeline. They have live access to
                your top jobs, recent resume builds, last 24h costs, and applications
                by status.
              </p>
              <p className="text-fg-subtle text-2xs uppercase tracking-wider mb-2">
                Try one of these
              </p>
              <ul className="flex flex-col gap-1.5">
                {SUGGESTIONS.map((s) => (
                  <li key={s}>
                    <button
                      type="button"
                      onClick={() => void send(s)}
                      className="text-left w-full px-3 py-2 rounded-md border border-border-strong text-fg hover:bg-surface-raised text-sm transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                    >
                      {s}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {!empty && (
            <ul className="flex flex-col gap-4">
              {messages.map((m, i) => (
                <li key={i} className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className={
                        m.role === 'user'
                          ? 'inline-flex items-center justify-center w-6 h-6 rounded-full bg-accent text-accent-fg text-2xs font-semibold'
                          : 'inline-flex items-center justify-center w-6 h-6 rounded-full bg-surface-raised text-fg text-2xs font-semibold border border-border'
                      }
                    >
                      {m.role === 'user' ? 'You' : 'B'}
                    </span>
                    <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
                      {m.role === 'user' ? 'You' : 'Boss'}
                    </span>
                  </div>
                  <div className="pl-8 text-fg whitespace-pre-wrap text-sm leading-relaxed">
                    {m.content}
                  </div>
                  {m.role === 'assistant' && (m.cost_usd != null || m.latency_ms != null) && (
                    <div className="pl-8 mt-1 flex items-center gap-2 text-3xs text-fg-subtle">
                      {m.cost_usd != null && <span>${m.cost_usd.toFixed(4)}</span>}
                      {m.cost_usd != null && m.latency_ms != null && <span aria-hidden="true">·</span>}
                      {m.latency_ms != null && <span>{(m.latency_ms / 1000).toFixed(1)}s</span>}
                      {m.model && (
                        <>
                          <span aria-hidden="true">·</span>
                          <span className="truncate max-w-[160px]" title={m.model}>
                            {m.model}
                          </span>
                        </>
                      )}
                    </div>
                  )}
                </li>
              ))}
              {pending && (
                <li className="flex flex-col gap-1">
                  <div className="flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-surface-raised text-fg text-2xs font-semibold border border-border"
                    >
                      B
                    </span>
                    <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
                      Boss
                    </span>
                  </div>
                  <div className="pl-8 text-fg-muted text-sm italic">Thinking…</div>
                </li>
              )}
            </ul>
          )}
        </div>

        <form onSubmit={onSubmit} className="border-t border-border bg-surface px-4 py-3 sm:px-6">
          <div className="flex items-end gap-2">
            <div className="flex-1 space-y-1">
              <label htmlFor={inputId} className="sr-only">
                Message
              </label>
              <textarea
                id={inputId}
                ref={textareaRef}
                placeholder="Ask the Boss anything about your pipeline…"
                rows={2}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                disabled={pending}
                aria-describedby={hintId}
                className={TEXTAREA_BASE}
              />
              <p id={hintId} className="text-3xs text-fg-subtle">
                Enter to send · Shift+Enter for newline
              </p>
            </div>
            <div className="flex flex-col gap-1">
              <Button
                type="submit"
                variant="primary"
                size="md"
                disabled={pending || !input.trim()}
                aria-label="Send message"
              >
                Send
              </Button>
              {!empty && (
                <Button
                  type="button"
                  variant="ghost"
                  size="xs"
                  onClick={clear}
                  disabled={pending}
                  aria-label="Clear conversation"
                >
                  Clear
                </Button>
              )}
            </div>
          </div>
        </form>
      </Card>

      {error && (
        <div role="alert">
          <Pill tone="danger">Error: {error}</Pill>
        </div>
      )}

      <LiveRegion>{announce}</LiveRegion>
    </div>
  )
}
