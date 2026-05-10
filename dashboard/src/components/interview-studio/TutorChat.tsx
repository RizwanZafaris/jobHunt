'use client'

/**
 * TutorChat — center pane chat UI.
 *
 * Stateless w.r.t. history: parent (StudioClient) owns the message array
 * because PrepMaterial's "Ask tutor about this" needs to push a prefilled
 * prompt. We accept history + onHistoryChange + a pendingPrompt slot.
 *
 * Behaviour:
 *  - Enter sends; Shift+Enter inserts a newline.
 *  - "Suggested next question" appears as a clickable chip below the last
 *    tutor message; clicking it submits that text immediately.
 *  - Loading state shows a 3-dot shimmer in a tutor bubble while we wait.
 *  - If the model recommends a different concept_level, we surface a small
 *    "Move to <level>?" hint and call onConceptLevelRecommend.
 *
 * Why not optimistic-insert the user message?  Because we want the server
 * to authoritatively persist + return ids for both messages. The window
 * between submit and reply is short enough (~3s) that a shimmer is fine.
 */
import { FormEvent, KeyboardEvent, useEffect, useRef, useState } from 'react'

import { tutorChat } from '@/lib/api/studio'
import {
  CONCEPT_LEVEL_LABEL,
  PREP_SECTION_LABEL,
  type ConceptLevel,
  type PrepSection,
  type TutorMessage,
} from '@/lib/types/interview-studio'
import { Button, Card, Pill, Textarea } from '@/components/ui'

interface TutorChatProps {
  applicationId: string
  conceptLevel: ConceptLevel
  history: TutorMessage[]
  onHistoryChange: (next: TutorMessage[]) => void
  onConceptLevelRecommend: (level: ConceptLevel) => void
  pendingPrompt: string
  onPendingPromptConsumed: () => void
}

const WAITING_BUBBLE_ID = '__waiting_for_tutor__'

function MessageBubble({ msg }: { msg: TutorMessage }) {
  const isUser = msg.role === 'user'
  const isWaiting = msg.id === WAITING_BUBBLE_ID
  return (
    <div className={isUser ? 'flex justify-end' : 'flex justify-start'}>
      <div
        className={
          isUser
            ? 'max-w-[85%] rounded-2xl bg-accent text-accent-fg px-3.5 py-2 text-xs leading-relaxed'
            : 'max-w-[92%] rounded-2xl bg-surface-raised text-fg border border-border px-3.5 py-2 text-xs leading-relaxed'
        }
      >
        {isWaiting ? (
          <span
            className="inline-flex items-center gap-1 text-fg-muted"
            aria-live="polite"
            aria-label="Tutor is typing"
          >
            <span className="h-1.5 w-1.5 rounded-full bg-fg-muted animate-pulse" />
            <span className="h-1.5 w-1.5 rounded-full bg-fg-muted animate-pulse [animation-delay:120ms]" />
            <span className="h-1.5 w-1.5 rounded-full bg-fg-muted animate-pulse [animation-delay:240ms]" />
          </span>
        ) : (
          <p className="whitespace-pre-wrap break-words">{msg.content}</p>
        )}
        {!isWaiting && msg.cited_section && (
          <div className="mt-1.5">
            <Pill tone="info" size="xs">
              cited: {PREP_SECTION_LABEL[msg.cited_section as PrepSection] || msg.cited_section}
            </Pill>
          </div>
        )}
      </div>
    </div>
  )
}

export default function TutorChat({
  applicationId,
  conceptLevel,
  history,
  onHistoryChange,
  onConceptLevelRecommend,
  pendingPrompt,
  onPendingPromptConsumed,
}: TutorChatProps) {
  const [input, setInput] = useState('')
  const [isSending, setIsSending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [recommended, setRecommended] = useState<ConceptLevel | null>(null)
  const scrollerRef = useRef<HTMLDivElement>(null)

  // Honour pendingPrompt — autofill the input, then the parent clears it.
  useEffect(() => {
    if (pendingPrompt) {
      setInput(pendingPrompt)
      onPendingPromptConsumed()
    }
  }, [pendingPrompt, onPendingPromptConsumed])

  // Auto-scroll to the bottom when the history changes.
  useEffect(() => {
    if (scrollerRef.current) {
      scrollerRef.current.scrollTop = scrollerRef.current.scrollHeight
    }
  }, [history.length, isSending])

  const lastTutor = [...history].reverse().find((m) => m.role === 'tutor' && m.id !== WAITING_BUBBLE_ID)
  const suggestedNext = lastTutor?.suggested_next || null

  async function send(text: string) {
    const trimmed = text.trim()
    if (!trimmed || isSending) return
    setError(null)
    setIsSending(true)

    const now = new Date().toISOString()
    const localUser: TutorMessage = {
      id: `local-user-${now}`,
      role: 'user',
      content: trimmed,
      concept_level: conceptLevel,
      cited_section: null,
      suggested_next: null,
      created_at: now,
    }
    const waitingBubble: TutorMessage = {
      id: WAITING_BUBBLE_ID,
      role: 'tutor',
      content: '',
      concept_level: conceptLevel,
      cited_section: null,
      suggested_next: null,
      created_at: now,
    }

    onHistoryChange([...history, localUser, waitingBubble])
    setInput('')

    try {
      const reply = await tutorChat(applicationId, trimmed, conceptLevel)
      const tutorMsg: TutorMessage = {
        id: reply.tutor_message_id || `tutor-${Date.now()}`,
        role: 'tutor',
        content: reply.response,
        concept_level: reply.concept_level_recommended,
        cited_section: reply.cited_section,
        suggested_next: reply.suggested_next_question,
        created_at: new Date().toISOString(),
      }
      const finalUser: TutorMessage = {
        ...localUser,
        id: reply.user_message_id || localUser.id,
      }
      onHistoryChange([...history, finalUser, tutorMsg])
      if (reply.concept_level_recommended && reply.concept_level_recommended !== conceptLevel) {
        setRecommended(reply.concept_level_recommended)
      } else {
        setRecommended(null)
      }
    } catch (e: unknown) {
      const errMsg = e instanceof Error ? e.message : 'Tutor reply failed'
      setError(errMsg)
      // Drop the waiting bubble; keep the user's message so they can retry.
      onHistoryChange([...history, localUser])
    } finally {
      setIsSending(false)
    }
  }

  function handleKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      void send(input)
    }
  }

  function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    void send(input)
  }

  return (
    <Card padding="md">
      <div
        ref={scrollerRef}
        className="space-y-2.5 max-h-[480px] overflow-y-auto pr-1 -mr-1"
        aria-live="polite"
      >
        {history.length === 0 ? (
          <p className="text-2xs text-fg-subtle text-center py-6">
            Ask the tutor anything — they cite the prep pack on the left.
            <br />
            Try: <em>&ldquo;walk me through likely behavioural questions&rdquo;</em>
          </p>
        ) : (
          history.map((m) => <MessageBubble key={m.id} msg={m} />)
        )}
      </div>

      {!isSending && suggestedNext && (
        <div className="mt-3">
          <button
            type="button"
            onClick={() => void send(suggestedNext)}
            className="w-full text-left text-2xs px-3 py-2 rounded-md border border-dashed border-border-strong text-fg-muted hover:bg-surface-raised hover:text-fg transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
          >
            <span className="text-fg-subtle">Suggested next:</span> {suggestedNext}
          </button>
        </div>
      )}

      {recommended && (
        <div className="mt-3 flex items-center justify-between gap-2 text-2xs text-fg-muted bg-info-bg/40 border border-info-border rounded-md px-3 py-2">
          <span>
            Tutor suggests <strong>{CONCEPT_LEVEL_LABEL[recommended]}</strong> next.
          </span>
          <Button
            size="xs"
            variant="secondary"
            onClick={() => {
              onConceptLevelRecommend(recommended)
              setRecommended(null)
            }}
          >
            Switch
          </Button>
        </div>
      )}

      <form onSubmit={handleSubmit} className="mt-3 space-y-2">
        <Textarea
          label="Message tutor"
          srLabel
          rows={3}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={handleKey}
          placeholder="Type your question. Enter to send · Shift+Enter for newline"
          disabled={isSending}
        />
        {error && (
          <p className="text-2xs text-danger" role="alert">
            {error}
          </p>
        )}
        <div className="flex items-center justify-between gap-2 text-2xs text-fg-subtle">
          <span>
            Engaging at <strong>{CONCEPT_LEVEL_LABEL[conceptLevel]}</strong>
          </span>
          <Button type="submit" size="sm" loading={isSending} disabled={isSending || !input.trim()}>
            Send
          </Button>
        </div>
      </form>
    </Card>
  )
}
