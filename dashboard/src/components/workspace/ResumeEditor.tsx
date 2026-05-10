/**
 * ResumeEditor — markdown textarea + chat assistant.
 *
 * Two-column on desktop:
 *   • Left (60%): editable textarea showing the current markdown.
 *     Edits are local until the user clicks Save.
 *   • Right (40%): chat panel. Each user turn fires ONE call to
 *     /workspace/{id}/edit-resume in the chosen mode and replaces the
 *     textarea with the response's `updated_md`. The assistant's
 *     `response` text shows in the chat thread.
 *
 * Three mode buttons (CONFIRMED with the user):
 *   1. Quick tweak    — primary, default. Single Opus call. Phase 2.
 *   2. Rebuild section — disabled, tooltip "Coming next session".
 *   3. Full rebuild   — disabled, tooltip "Coming next session".
 *
 * Save persists `user_edited_md` to the resume_builds row. Cancel
 * reverts to the build's last saved state (the prop `initialMarkdown`).
 *
 * Chat history is component state only — Phase 2 deliberately defers
 * persistence (see api/WORKSPACE.md).
 */
'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Button } from '@/components/ui/Button'
import { Card } from '@/components/ui/Card'
import { Icon } from '@/components/ui/Icon'
import { Pill } from '@/components/ui/Pill'
import { LiveRegion } from '@/components/ui/LiveRegion'
import { editResume, saveResumeEdit } from '@/lib/api/workspace'
import type {
  EditChatMessage,
  EditChatRole,
  ResumeArtifact,
  ResumeEditMode,
} from '@/lib/types/workspace'

const TEXTAREA_CLS =
  'w-full bg-surface text-fg placeholder:text-fg-subtle border border-border-strong rounded-md px-3 py-2 text-xs font-mono leading-relaxed ' +
  'focus-visible:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30 ' +
  'disabled:opacity-50 transition-colors'

const PROSE_CLS =
  'text-fg text-xs leading-relaxed ' +
  '[&_h1]:text-base [&_h1]:font-semibold [&_h1]:mt-0 [&_h1]:mb-1.5 [&_h1]:text-fg ' +
  '[&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1 [&_h2]:text-fg ' +
  '[&_h3]:text-xs [&_h3]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:text-fg ' +
  '[&_p]:my-1.5 [&_ul]:list-disc [&_ul]:pl-4 [&_ul]:my-1.5 [&_li]:my-0.5 ' +
  '[&_strong]:font-semibold [&_strong]:text-fg ' +
  '[&_a]:text-accent [&_a:hover]:underline'

const COMING_TOOLTIP = 'Coming next session — see WORKSPACE.md for the contract.'

const QUICK_TWEAK_EXAMPLES = [
  'Tighten the second Daraz bullet to one line.',
  'Replace "leveraged" with a more concrete verb.',
  'Move the AWS skill into the top of the skills list.',
  'Cut the Education section to one line per degree.',
  'Reword the summary to lead with payments fraud experience.',
]

function nowIso() {
  return new Date().toISOString()
}

function generateId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export interface ResumeEditorProps {
  jobId: number
  buildId: string
  initialMarkdown: string
  onCancel: () => void
  onSaved: (resume: ResumeArtifact) => void
}

export function ResumeEditor({
  jobId,
  buildId,
  initialMarkdown,
  onCancel,
  onSaved,
}: ResumeEditorProps) {
  const [markdown, setMarkdown] = useState<string>(initialMarkdown)
  const [originalMarkdown] = useState<string>(initialMarkdown)
  const [showPreview, setShowPreview] = useState(false)
  const [chat, setChat] = useState<EditChatMessage[]>([])
  const [input, setInput] = useState<string>('')
  const [mode, setMode] = useState<ResumeEditMode>('quick_tweak')
  const [chatBusy, setChatBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [statusMsg, setStatusMsg] = useState<string>('')

  const dirty = markdown !== originalMarkdown

  const totalCost = useMemo(
    () => chat.reduce((sum, m) => sum + (typeof m.cost_usd === 'number' ? m.cost_usd : 0), 0),
    [chat],
  )

  // Auto-scroll the chat panel as turns come in.
  const chatEndRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [chat.length, chatBusy])

  const handleChatSend = useCallback(async () => {
    const instruction = input.trim()
    if (!instruction || chatBusy) return
    setErrorMsg(null)

    const userTurn: EditChatMessage = {
      id: generateId(),
      role: 'user',
      content: instruction,
      created_at: nowIso(),
    }
    setChat((prev) => [...prev, userTurn])
    setInput('')

    if (mode !== 'quick_tweak') {
      // Mode is disabled — but a defensive guard keeps the UI honest if
      // the disabled state is bypassed somehow.
      const stub: EditChatMessage = {
        id: generateId(),
        role: 'assistant',
        content: COMING_TOOLTIP,
        created_at: nowIso(),
      }
      setChat((prev) => [...prev, stub])
      return
    }

    setChatBusy(true)
    try {
      const history: { role: EditChatRole; content: string }[] = chat.map((m) => ({
        role: m.role,
        content: m.content,
      }))
      const result = await editResume(jobId, {
        instruction,
        current_md: markdown,
        chat_history: history,
        mode: 'quick_tweak',
      })
      // If the model produced new markdown, swap it in. Otherwise keep
      // current — quick_tweak's contract guarantees `updated_md` is
      // either the new version or the input verbatim.
      if (result.updated_md && result.updated_md !== markdown) {
        setMarkdown(result.updated_md)
      }
      const reply: EditChatMessage = {
        id: generateId(),
        role: 'assistant',
        content: result.response || 'Done.',
        cost_usd: result.cost_usd,
        fixes_applied: result.fixes_applied,
        created_at: nowIso(),
      }
      setChat((prev) => [...prev, reply])
      setStatusMsg('Edit applied — review on the left, then Save.')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      const reply: EditChatMessage = {
        id: generateId(),
        role: 'assistant',
        content: `I couldn't apply that edit. ${msg}`,
        created_at: nowIso(),
      }
      setChat((prev) => [...prev, reply])
      setErrorMsg(msg)
    } finally {
      setChatBusy(false)
    }
  }, [input, chat, chatBusy, mode, jobId, markdown])

  const handleSave = useCallback(async () => {
    if (!dirty) {
      setStatusMsg('Nothing to save.')
      return
    }
    setSaving(true)
    setErrorMsg(null)
    try {
      const saved = await saveResumeEdit(jobId, markdown, buildId)
      // Construct the next ResumeArtifact slice without re-fetching —
      // the parent's view will update via onSaved.
      const next: ResumeArtifact = {
        build_id: saved.build_id,
        status: 'converged',
        user_edited_md: markdown,
        user_edited_at: saved.user_edited_at ?? nowIso(),
      }
      onSaved(next)
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err))
      setSaving(false)
    }
  }, [dirty, jobId, markdown, buildId, onSaved])

  const handleCancel = useCallback(() => {
    if (dirty) {
      const ok = window.confirm('Discard your unsaved edits?')
      if (!ok) return
    }
    onCancel()
  }, [dirty, onCancel])

  const handleSuggestion = useCallback((s: string) => {
    setInput(s)
  }, [])

  return (
    <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
      {/* Left column — markdown editor */}
      <div className="lg:col-span-3 space-y-3">
        <Card padding="md">
          <div className="flex items-center gap-2 flex-wrap mb-2">
            <Pill tone={dirty ? 'warning' : 'neutral'} size="xs">
              {dirty ? 'Unsaved edits' : 'Saved'}
            </Pill>
            <span className="text-2xs text-fg-subtle tnum">
              {markdown.length.toLocaleString()} chars
            </span>
            <button
              type="button"
              className="text-2xs font-medium text-fg-muted hover:text-fg ml-auto"
              onClick={() => setShowPreview((v) => !v)}
              aria-pressed={showPreview}
            >
              {showPreview ? 'Show source' : 'Preview rendered'}
            </button>
          </div>
          {showPreview ? (
            <div className="bg-surface-raised rounded-md p-3 border border-border min-h-[24rem] max-h-[36rem] overflow-y-auto">
              <article className={PROSE_CLS}>
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{markdown}</ReactMarkdown>
              </article>
            </div>
          ) : (
            <label className="block">
              <span className="sr-only">Resume markdown</span>
              <textarea
                value={markdown}
                onChange={(e) => setMarkdown(e.target.value)}
                rows={26}
                className={TEXTAREA_CLS}
                spellCheck
                aria-label="Resume markdown"
              />
            </label>
          )}
          <div className="mt-3 flex items-center gap-2 flex-wrap">
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              disabled={!dirty || saving}
              loading={saving}
            >
              <Icon name="check" size={12} />
              Save
            </Button>
            <Button variant="ghost" size="sm" onClick={handleCancel} disabled={saving}>
              Cancel
            </Button>
            {dirty && !saving && (
              <span className="text-2xs text-fg-subtle">
                Edits stay local until you click Save.
              </span>
            )}
          </div>
        </Card>
      </div>

      {/* Right column — chat panel */}
      <div className="lg:col-span-2 space-y-3">
        <Card
          padding="md"
          title="Edit with AI"
          description="Describe what to change. The assistant edits surgically — your formatting and other sections stay put."
        >
          {/* Mode picker */}
          <div role="radiogroup" aria-label="Edit mode" className="flex items-center gap-1.5 flex-wrap mb-3">
            <ModeButton
              label="Quick tweak"
              active={mode === 'quick_tweak'}
              hint="~$0.05, ~3s. Single Opus call."
              onSelect={() => setMode('quick_tweak')}
            />
            <ModeButton
              label="Rebuild section"
              active={mode === 'rebuild_section'}
              disabled
              hint={COMING_TOOLTIP}
              onSelect={() => undefined}
            />
            <ModeButton
              label="Full rebuild"
              active={mode === 'full_rebuild'}
              disabled
              hint={COMING_TOOLTIP}
              onSelect={() => undefined}
            />
          </div>

          {/* Chat thread */}
          <div
            className="bg-surface-raised border border-border rounded-md p-3 space-y-2 max-h-[20rem] overflow-y-auto"
            aria-live="polite"
            aria-label="Edit assistant chat"
          >
            {chat.length === 0 ? (
              <div className="space-y-2">
                <p className="text-2xs text-fg-muted leading-relaxed">
                  Try one of these — or type your own:
                </p>
                <ul className="flex flex-col gap-1">
                  {QUICK_TWEAK_EXAMPLES.map((ex) => (
                    <li key={ex}>
                      <button
                        type="button"
                        onClick={() => handleSuggestion(ex)}
                        className="w-full text-left text-2xs text-fg-muted hover:text-fg bg-surface border border-border rounded px-2 py-1 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        “{ex}”
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              chat.map((m) => <ChatBubble key={m.id} message={m} />)
            )}
            {chatBusy && (
              <div className="flex items-center gap-2 text-2xs text-fg-muted">
                <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-accent" />
                Editing…
              </div>
            )}
            <div ref={chatEndRef} />
          </div>

          {/* Composer */}
          <div className="mt-3 flex items-end gap-2">
            <label className="flex-1">
              <span className="sr-only">Instruction</span>
              <textarea
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                    e.preventDefault()
                    void handleChatSend()
                  }
                }}
                rows={2}
                placeholder="e.g. tighten the second Daraz bullet to one line"
                className={TEXTAREA_CLS.replace('font-mono ', '')}
                disabled={chatBusy || mode !== 'quick_tweak'}
              />
            </label>
            <Button
              variant="primary"
              size="sm"
              onClick={handleChatSend}
              disabled={!input.trim() || chatBusy || mode !== 'quick_tweak'}
              loading={chatBusy}
            >
              Send
            </Button>
          </div>
          <p className="mt-1 text-3xs text-fg-subtle">
            Cmd/Ctrl + Enter to send · {totalCost > 0 ? `$${totalCost.toFixed(4)} this session` : 'no cost yet'}
          </p>
          {errorMsg && (
            <p className="mt-2 text-2xs text-danger" role="alert">
              {errorMsg}
            </p>
          )}
        </Card>
      </div>

      <LiveRegion>{statusMsg}</LiveRegion>
    </div>
  )
}

function ModeButton({
  label,
  hint,
  active,
  disabled,
  onSelect,
}: {
  label: string
  hint?: string
  active: boolean
  disabled?: boolean
  onSelect: () => void
}) {
  const cls = [
    'inline-flex items-center px-2.5 py-1 rounded-md text-2xs font-medium border transition-colors',
    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
    active && !disabled
      ? 'bg-accent text-accent-fg border-accent'
      : 'bg-surface text-fg-muted border-border-strong hover:bg-surface-raised',
    disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer',
  ].filter(Boolean).join(' ')
  return (
    <button
      type="button"
      role="radio"
      aria-checked={active}
      aria-disabled={disabled || undefined}
      disabled={disabled}
      onClick={onSelect}
      title={hint}
      className={cls}
    >
      {label}
    </button>
  )
}

function ChatBubble({ message }: { message: EditChatMessage }) {
  const mine = message.role === 'user'
  return (
    <div className={mine ? 'flex justify-end' : 'flex justify-start'}>
      <div
        className={[
          'max-w-[90%] rounded-md px-2.5 py-1.5 text-2xs leading-relaxed',
          mine
            ? 'bg-accent text-accent-fg'
            : 'bg-surface text-fg border border-border',
        ].join(' ')}
      >
        <p className="whitespace-pre-wrap">{message.content}</p>
        {!mine && message.fixes_applied && message.fixes_applied.length > 0 && (
          <ul className="mt-1.5 space-y-0.5">
            {message.fixes_applied.map((f, i) => (
              <li key={`${message.id}-fix-${i}`} className="flex items-start gap-1 text-3xs text-fg-muted">
                <Icon name="check" size={9} className="mt-[2px] shrink-0" />
                <span>{f}</span>
              </li>
            ))}
          </ul>
        )}
        {!mine && typeof message.cost_usd === 'number' && (
          <p className="mt-1 text-3xs text-fg-subtle tnum">
            ${message.cost_usd.toFixed(4)}
          </p>
        )}
      </div>
    </div>
  )
}

export default ResumeEditor
