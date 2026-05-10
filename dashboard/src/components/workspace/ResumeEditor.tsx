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
import {
  editResume,
  fetchJobsRun,
  fetchWorkspace,
  fullRebuildResume,
  rebuildSection,
  saveResumeEdit,
} from '@/lib/api/workspace'
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

const QUICK_TWEAK_HINT = '~$0.05, ~3s. Single Opus call — surgical edit.'
const REBUILD_SECTION_HINT = '~$0.50, ~30-60s. Mini-graph (writer → critic → polish) over one H2 section.'
const FULL_REBUILD_HINT = '~$1, ~3-5 min. Full G2 ensemble from scratch. Use sparingly.'

const QUICK_TWEAK_EXAMPLES = [
  'Tighten the second Daraz bullet to one line.',
  'Replace "leveraged" with a more concrete verb.',
  'Move the AWS skill into the top of the skills list.',
  'Cut the Education section to one line per degree.',
  'Reword the summary to lead with payments fraud experience.',
]

const REBUILD_SECTION_EXAMPLES = [
  'Make the Experience section more product-led with metrics.',
  'Re-shape Skills to mirror the JD’s top-10 must-haves.',
  'Tighten Summary to lead with payments-fraud experience and outcomes.',
]

const FULL_REBUILD_EXAMPLES = [
  'Rewrite from scratch leaning into platform-engineering achievements.',
  'Re-tailor end-to-end for a senior PM track at this company.',
  'Restructure into a 1-pager with quantified outcomes throughout.',
]

/**
 * Heuristic markdown H2 parser — must mirror the Python
 * `extract_section` regex in agents/resume_edit_assistant.py so the UI
 * picker offers exactly the headings the backend can splice on.
 */
const H2_LINE_RE = /^[ \t]{0,3}##[ \t]+(.+?)[ \t]*$/gm

function listH2Headings(md: string): string[] {
  if (!md) return []
  const out: string[] = []
  H2_LINE_RE.lastIndex = 0
  let m: RegExpExecArray | null
  while ((m = H2_LINE_RE.exec(md)) !== null) {
    const title = (m[1] || '').trim()
    if (title) out.push(title)
  }
  return out
}

const FULL_REBUILD_POLL_INTERVAL_MS = 8000
const FULL_REBUILD_POLL_TIMEOUT_MS = 10 * 60 * 1000  // 10 min ceiling.

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
  // Setter exists so a successful Full rebuild can rebase the "saved"
  // baseline to the freshly-built resume — clearing dirty state without
  // unmounting the editor.
  const [originalMarkdown, setOriginalMarkdown] = useState<string>(initialMarkdown)
  const [showPreview, setShowPreview] = useState(false)
  const [chat, setChat] = useState<EditChatMessage[]>([])
  const [input, setInput] = useState<string>('')
  const [mode, setMode] = useState<ResumeEditMode>('quick_tweak')
  const [chatBusy, setChatBusy] = useState(false)
  const [saving, setSaving] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [statusMsg, setStatusMsg] = useState<string>('')

  // Rebuild-section flow — when active, the user picks ONE of the H2
  // sections detected in the current markdown.
  const [pickerOpen, setPickerOpen] = useState(false)
  const [pickedSection, setPickedSection] = useState<string | null>(null)

  // Full-rebuild flow — async via /jobs-runs/{run_id} polling.
  const [fullRebuildRunId, setFullRebuildRunId] = useState<string | null>(null)
  const [fullRebuildElapsed, setFullRebuildElapsed] = useState<number>(0)

  const dirty = markdown !== originalMarkdown
  const h2Headings = useMemo(() => listH2Headings(markdown), [markdown])

  const totalCost = useMemo(
    () => chat.reduce((sum, m) => sum + (typeof m.cost_usd === 'number' ? m.cost_usd : 0), 0),
    [chat],
  )

  // Auto-scroll the chat panel as turns come in.
  const chatEndRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [chat.length, chatBusy])

  /** Append a user turn + clear the composer. Returns the instruction. */
  const appendUserTurn = useCallback((instruction: string): string => {
    const turn: EditChatMessage = {
      id: generateId(),
      role: 'user',
      content: instruction,
      created_at: nowIso(),
    }
    setChat((prev) => [...prev, turn])
    setInput('')
    return instruction
  }, [])

  const appendAssistantTurn = useCallback(
    (content: string, extras?: Partial<EditChatMessage>) => {
      const reply: EditChatMessage = {
        id: generateId(),
        role: 'assistant',
        content,
        created_at: nowIso(),
        ...extras,
      }
      setChat((prev) => [...prev, reply])
    },
    [],
  )

  const runQuickTweak = useCallback(
    async (instruction: string) => {
      setChatBusy(true)
      try {
        const history: { role: EditChatRole; content: string }[] = chat.map(
          (m) => ({ role: m.role, content: m.content }),
        )
        const result = await editResume(jobId, {
          instruction,
          current_md: markdown,
          chat_history: history,
          mode: 'quick_tweak',
        })
        if (result.updated_md && result.updated_md !== markdown) {
          setMarkdown(result.updated_md)
        }
        appendAssistantTurn(result.response || 'Done.', {
          cost_usd: result.cost_usd,
          fixes_applied: result.fixes_applied,
        })
        setStatusMsg('Edit applied — review on the left, then Save.')
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        appendAssistantTurn(`I couldn't apply that edit. ${msg}`)
        setErrorMsg(msg)
      } finally {
        setChatBusy(false)
      }
    },
    [chat, jobId, markdown, appendAssistantTurn],
  )

  const runRebuildSection = useCallback(
    async (instruction: string, section: string) => {
      setChatBusy(true)
      setStatusMsg(`Rebuilding the ${section} section… (~30-60s)`)
      try {
        const result = await rebuildSection(jobId, {
          section,
          edit_intent: instruction,
          current_md: markdown,
        })
        if (result.updated_md && result.updated_md !== markdown) {
          setMarkdown(result.updated_md)
        }
        appendAssistantTurn(
          result.response || `Rebuilt the ${section} section.`,
          {
            cost_usd: result.cost_usd,
            fixes_applied: result.fixes_applied,
          },
        )
        setStatusMsg(
          `Section rebuild complete — review the ${section} block on the left, then Save.`,
        )
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        appendAssistantTurn(
          `Section rebuild failed. ${msg}\n\nTry a smaller section, or fall back to Quick tweak.`,
        )
        setErrorMsg(msg)
      } finally {
        setChatBusy(false)
        setPickerOpen(false)
        setPickedSection(null)
      }
    },
    [jobId, markdown, appendAssistantTurn],
  )

  /**
   * Poll /jobs-runs/{run_id} until the run terminates. Replaces
   * markdown on success; surfaces the error on failure.
   */
  const runFullRebuild = useCallback(
    async (instruction: string) => {
      setChatBusy(true)
      setStatusMsg('Queuing a full rebuild (this takes ~3-5 min)…')
      let runId: string
      try {
        const enq = await fullRebuildResume(jobId, {
          edit_intent: instruction.trim() ? instruction : undefined,
        })
        runId = enq.run_id
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        appendAssistantTurn(`Couldn't start the full rebuild. ${msg}`)
        setErrorMsg(msg)
        setChatBusy(false)
        return
      }

      setFullRebuildRunId(runId)
      setFullRebuildElapsed(0)
      const startedAt = Date.now()
      let cancelled = false

      // Poll loop. Non-blocking — we let the user keep editing the
      // textarea while the rebuild churns; if they save, we no-op.
      const tick = async (): Promise<void> => {
        if (cancelled) return
        const elapsed = Date.now() - startedAt
        setFullRebuildElapsed(elapsed)
        if (elapsed > FULL_REBUILD_POLL_TIMEOUT_MS) {
          setErrorMsg('Full rebuild took longer than 10 minutes. Check the run status.')
          appendAssistantTurn(
            'Full rebuild is still running after 10 minutes. Check the runs panel — '
              + 'it may complete in the background.',
          )
          setChatBusy(false)
          setFullRebuildRunId(null)
          return
        }
        try {
          const row = await fetchJobsRun(runId)
          if (row.status === 'succeeded') {
            // Auto-replace path. Refetch the workspace bundle to get the
            // freshly-built resume artifact + markdown, then either splice
            // it into the editor (clean state) or ask the user (dirty).
            try {
              const ws = await fetchWorkspace(jobId)
              const fresh = ws.resume
              const freshMd = (fresh?.user_edited_md ?? fresh?.resume_md ?? '').toString()

              // Compute dirty against the snapshot the user is staring at.
              // We compare the LIVE markdown to the LAST-SAVED baseline — if
              // the user has unsaved edits, we ask before clobbering.
              const editorIsDirty = markdown !== originalMarkdown

              if (!fresh || !freshMd) {
                // The build succeeded but we couldn't pull the artifact —
                // fall back to the manual-reload prompt.
                appendAssistantTurn(
                  'Full rebuild complete, but the new resume hasn’t synced yet. '
                    + 'Reload the workspace in a few seconds to view it.',
                  {},
                )
                setStatusMsg(
                  'Full rebuild succeeded. Reload to pick up the new build.',
                )
              } else if (editorIsDirty && freshMd !== markdown) {
                const proceed = typeof window !== 'undefined'
                  ? window.confirm(
                      'Full rebuild is ready. You have unsaved edits in the '
                      + 'editor — replace them with the rebuilt resume? '
                      + '(Your unsaved edits will be discarded.)',
                    )
                  : true
                if (proceed) {
                  setMarkdown(freshMd)
                  setOriginalMarkdown(freshMd)
                  onSaved(fresh)
                  appendAssistantTurn(
                    'Replaced the editor with the rebuilt resume. '
                      + 'Your unsaved edits were discarded as confirmed.',
                    {},
                  )
                  setStatusMsg('Editor synced to the rebuilt resume.')
                } else {
                  appendAssistantTurn(
                    'Full rebuild is ready but the editor still shows your '
                      + 'unsaved draft. Save or discard, then reload to pick '
                      + 'up the new build.',
                    {},
                  )
                  setStatusMsg(
                    'Full rebuild succeeded. Editor kept your unsaved edits.',
                  )
                }
              } else {
                // Clean state (or no diff against fresh) — splice silently.
                setMarkdown(freshMd)
                setOriginalMarkdown(freshMd)
                onSaved(fresh)
                appendAssistantTurn(
                  'Full rebuild complete. Editor now shows the rebuilt resume.',
                  {},
                )
                setStatusMsg('Full rebuild applied to the editor.')
              }
            } catch (refreshErr) {
              const msg = refreshErr instanceof Error ? refreshErr.message : String(refreshErr)
              appendAssistantTurn(
                'Full rebuild succeeded but I couldn’t refresh the editor — '
                  + msg + '. Reload the workspace manually.',
                {},
              )
              setStatusMsg('Full rebuild succeeded. Reload manually to view.')
            }
            setChatBusy(false)
            setFullRebuildRunId(null)
            return
          }
          if (row.status === 'failed' || row.status === 'cancelled') {
            const errMsg =
              (row.last_error && row.last_error.slice(0, 240))
                || `Run ended with status=${row.status}.`
            appendAssistantTurn(`Full rebuild failed. ${errMsg}`)
            setErrorMsg(errMsg)
            setChatBusy(false)
            setFullRebuildRunId(null)
            return
          }
          // Still queued / running — keep going.
          window.setTimeout(() => {
            void tick()
          }, FULL_REBUILD_POLL_INTERVAL_MS)
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err)
          appendAssistantTurn(`Lost track of the full rebuild. ${msg}`)
          setErrorMsg(msg)
          setChatBusy(false)
          setFullRebuildRunId(null)
        }
      }
      window.setTimeout(() => {
        void tick()
      }, FULL_REBUILD_POLL_INTERVAL_MS)
      // Component-unmount safety: best-effort, parent's onCancel covers
      // most paths.
      return () => {
        cancelled = true
      }
    },
    [jobId, appendAssistantTurn],
  )

  const handleChatSend = useCallback(async () => {
    const instructionRaw = input.trim()
    setErrorMsg(null)

    if (mode === 'quick_tweak') {
      if (!instructionRaw || chatBusy) return
      const instruction = appendUserTurn(instructionRaw)
      await runQuickTweak(instruction)
      return
    }

    if (mode === 'rebuild_section') {
      if (h2Headings.length === 0) {
        appendAssistantTurn(
          'No H2 sections found in this resume — Quick tweak is the right tool here.',
        )
        return
      }
      // First click: open the picker. Second click (after a section is
      // picked): fire the rebuild. The picker button submits via
      // handleRebuildSectionConfirm; this branch shouldn't fire if the
      // composer is the entry path.
      if (!pickedSection) {
        setPickerOpen(true)
        return
      }
      if (chatBusy) return
      const intent = instructionRaw || `Improve the ${pickedSection} section.`
      const instruction = appendUserTurn(intent)
      await runRebuildSection(instruction, pickedSection)
      return
    }

    if (mode === 'full_rebuild') {
      if (chatBusy) return
      const ok = window.confirm(
        'This regenerates your resume from scratch and may take ~3-5 minutes.\n\n'
          + 'It costs roughly $1 and runs the full ensemble pipeline.\n\nContinue?',
      )
      if (!ok) return
      const intent = instructionRaw || 'Re-tailor end-to-end.'
      const instruction = appendUserTurn(intent)
      await runFullRebuild(instruction)
      return
    }
  }, [
    input,
    mode,
    chatBusy,
    pickedSection,
    h2Headings,
    appendUserTurn,
    appendAssistantTurn,
    runQuickTweak,
    runRebuildSection,
    runFullRebuild,
  ])

  const handleRebuildSectionConfirm = useCallback(
    async (section: string) => {
      const intent =
        input.trim() || `Improve the ${section} section.`
      setPickedSection(section)
      setPickerOpen(false)
      const instruction = appendUserTurn(intent)
      await runRebuildSection(instruction, section)
    },
    [input, appendUserTurn, runRebuildSection],
  )

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
              hint={QUICK_TWEAK_HINT}
              onSelect={() => {
                setMode('quick_tweak')
                setPickerOpen(false)
                setPickedSection(null)
              }}
            />
            <ModeButton
              label="Rebuild section"
              active={mode === 'rebuild_section'}
              hint={REBUILD_SECTION_HINT}
              onSelect={() => {
                setMode('rebuild_section')
                setPickerOpen(true)
                setPickedSection(null)
              }}
            />
            <ModeButton
              label="Full rebuild"
              active={mode === 'full_rebuild'}
              hint={FULL_REBUILD_HINT}
              onSelect={() => {
                setMode('full_rebuild')
                setPickerOpen(false)
                setPickedSection(null)
              }}
            />
          </div>

          {/* Rebuild section picker — only when mode is rebuild_section */}
          {mode === 'rebuild_section' && pickerOpen && (
            <div
              className="mb-3 rounded-md border border-border bg-surface-raised p-3"
              role="region"
              aria-label="Pick a section to rebuild"
            >
              <p className="text-2xs font-medium text-fg mb-1.5">
                Pick a section to rebuild
              </p>
              {h2Headings.length === 0 ? (
                <p className="text-2xs text-fg-muted">
                  No H2 sections found. Quick tweak is the right tool — switch back.
                </p>
              ) : (
                <ul className="space-y-1">
                  {h2Headings.map((h) => (
                    <li key={h}>
                      <button
                        type="button"
                        onClick={() => {
                          void handleRebuildSectionConfirm(h)
                        }}
                        disabled={chatBusy}
                        className="w-full text-left text-2xs text-fg bg-surface border border-border-strong rounded px-2 py-1 hover:bg-accent/10 hover:border-accent transition-colors disabled:opacity-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
                      >
                        <span className="font-medium">{h}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-2 text-3xs text-fg-subtle">
                Type your edit intent above first (e.g. "lead with payments-fraud"),
                then click the section you want rebuilt. ~30-60s per rebuild.
              </p>
            </div>
          )}

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
                  {(mode === 'quick_tweak'
                    ? QUICK_TWEAK_EXAMPLES
                    : mode === 'rebuild_section'
                      ? REBUILD_SECTION_EXAMPLES
                      : FULL_REBUILD_EXAMPLES
                  ).map((ex) => (
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
                {mode === 'quick_tweak' && 'Editing…'}
                {mode === 'rebuild_section' && 'Rebuilding section…'}
                {mode === 'full_rebuild' && (
                  <>
                    Full rebuild running{fullRebuildRunId ? ` (${Math.floor(fullRebuildElapsed / 1000)}s elapsed)` : ''}…
                  </>
                )}
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
                placeholder={
                  mode === 'quick_tweak'
                    ? 'e.g. tighten the second Daraz bullet to one line'
                    : mode === 'rebuild_section'
                      ? 'Edit intent for the section (e.g. "lead with payments-fraud, more metrics"). Then click a section above.'
                      : 'Optional intent for the full rebuild (e.g. "lean into platform-engineering achievements").'
                }
                className={TEXTAREA_CLS.replace('font-mono ', '')}
                disabled={chatBusy}
              />
            </label>
            <Button
              variant="primary"
              size="sm"
              onClick={handleChatSend}
              disabled={
                chatBusy
                || (mode === 'quick_tweak' && !input.trim())
                || (mode === 'rebuild_section' && h2Headings.length === 0)
              }
              loading={chatBusy}
            >
              {mode === 'quick_tweak' && 'Send'}
              {mode === 'rebuild_section' && (pickerOpen ? 'Pick section' : 'Pick a section')}
              {mode === 'full_rebuild' && 'Start full rebuild'}
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
