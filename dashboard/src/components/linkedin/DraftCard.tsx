/**
 * DraftCard — single LinkedIn draft card on /linkedin.
 *
 * Layout:
 *   ┌──────────────────────────────────────────────────────────┐
 *   │ angle-pill   company   "why this works" tip   status     │
 *   │ HOOK (bold, 2-3 lines)                                   │
 *   │ BODY (3 lines truncated, "Show more" expands)            │
 *   │ #hashtags as small chips                                  │
 *   │ source-news link                                          │
 *   │ ---------------------------------------------------------│
 *   │ [Edit] [Copy] [Approve & Schedule] [Reject]              │
 *   └──────────────────────────────────────────────────────────┘
 *
 * Inline edit toggles a textarea over hook+body. The Copy button calls
 * /linkedin/drafts/:id/copy AND copies to the clipboard locally
 * (the API records `manual_copy_at`; the clipboard write is what
 * actually delivers the post to LinkedIn).
 *
 * TODO: replace handler stubs with real fetch calls — see
 *       _pending_endpoints.md and api/LINKEDIN.md.
 */
'use client'

import type { CSSProperties } from 'react'
import { useState } from 'react'
import { clsx } from 'clsx'
import { Button, Icon, Pill, type PillTone } from '@/components/ui'
import {
  ANGLE_LABEL,
  IMAGE_BRIEF_KIND_LABEL,
  type ImageBrief,
  type LinkedInAngle,
  type LinkedInDraft,
  type LinkedInDraftStatus,
} from '@/lib/types/linkedin'
import {
  approveLinkedInDraft,
  patchLinkedInDraft,
  recordCopyLinkedInDraft,
  rejectLinkedInDraft,
} from '@/lib/api'

const ANGLE_TONE: Record<LinkedInAngle, PillTone> = {
  news_commentary: 'info',
  contrarian_take: 'warning',
  build_in_public: 'success',
  lesson_learned: 'accent',
  industry_analysis: 'neutral',
}

const STATUS_TONE: Record<LinkedInDraftStatus, PillTone> = {
  draft: 'neutral',
  approved: 'info',
  scheduled: 'info',
  posted: 'success',
  rejected: 'danger',
  expired: 'warning',
}

const STATUS_LABEL: Record<LinkedInDraftStatus, string> = {
  draft: 'Draft',
  approved: 'Approved',
  scheduled: 'Scheduled',
  posted: 'Posted',
  rejected: 'Rejected',
  expired: 'Expired',
}

const BODY_TRUNCATE_LINES = 3

function formatScheduled(iso: string | null): string | null {
  if (!iso) return null
  try {
    const d = new Date(iso)
    // BUG-017: pin timeZone to UTC so SSR and CSR produce the same string.
    return d.toLocaleString('en-US', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      hour: '2-digit',
      minute: '2-digit',
      timeZone: 'UTC',
      hour12: false,
    })
  } catch {
    return iso
  }
}

export interface DraftCardProps {
  draft: LinkedInDraft
  /** Lifts state changes (status / edits) up to the page. Optional. */
  onChange?: (next: LinkedInDraft) => void
}

export function DraftCard({ draft, onChange }: DraftCardProps) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [hook, setHook] = useState(draft.hook)
  const [body, setBody] = useState(draft.body)
  const [busy, setBusy] = useState<null | 'copy' | 'approve' | 'reject' | 'save'>(null)
  const [feedback, setFeedback] = useState<string | null>(null)

  const angleLabel = ANGLE_LABEL[draft.angle]
  const angleTone: PillTone = ANGLE_TONE[draft.angle]
  const statusLabel = STATUS_LABEL[draft.status]
  const statusTone: PillTone = STATUS_TONE[draft.status]
  const scheduled = formatScheduled(draft.scheduledFor)

  // 2026-05-14 harden: all four handlers were mocks (TODO comments).
  // Now wired to real backend with validation + error handling.

  const handleEditSave = async () => {
    if (!hook.trim()) {
      setFeedback('Hook cannot be empty.')
      setTimeout(() => setFeedback(null), 3000)
      return
    }
    if (!body.trim()) {
      setFeedback('Body cannot be empty.')
      setTimeout(() => setFeedback(null), 3000)
      return
    }
    setBusy('save')
    setFeedback('Saving…')
    try {
      const updated = await patchLinkedInDraft(draft.id, { hook, body })
      onChange?.(updated)
      setFeedback('Saved')
      setEditing(false)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      setFeedback(msg.slice(0, 140))
    } finally {
      setBusy(null)
      setTimeout(() => setFeedback(null), 3500)
    }
  }

  const handleCopy = async () => {
    setBusy('copy')
    const text = [hook, '', body, draft.cta ?? '', '', draft.hashtags.join(' ')]
      .filter(Boolean)
      .join('\n')
    try {
      if (typeof navigator !== 'undefined' && navigator.clipboard) {
        await navigator.clipboard.writeText(text)
      } else {
        throw new Error('clipboard API unavailable')
      }
      // Server-side telemetry — non-fatal if it fails.
      const updated = await recordCopyLinkedInDraft(draft.id)
      if (updated) {
        onChange?.(updated)
      } else {
        // 2026-05-14 fix: was auto-setting status='posted' on any copy of
        // an approved/scheduled draft. Drafts can be copied multiple times
        // before actually posting — leave status alone, only update the
        // manualCopyAt timestamp locally.
        onChange?.({ ...draft, manualCopyAt: new Date().toISOString() })
      }
      setFeedback('Copied. Open LinkedIn and paste.')
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Copy failed'
      setFeedback(`Copy failed — ${msg.slice(0, 120)}`)
    } finally {
      setBusy(null)
      setTimeout(() => setFeedback(null), 3500)
    }
  }

  const handleApprove = async () => {
    if (!hook.trim() || !body.trim()) {
      setFeedback('Cannot approve an empty draft — edit it first.')
      setTimeout(() => setFeedback(null), 3500)
      return
    }
    setBusy('approve')
    setFeedback('Approving…')
    try {
      const updated = await approveLinkedInDraft(draft.id)
      onChange?.(updated)
      setFeedback(
        updated.scheduledFor
          ? `Scheduled for ${formatScheduled(updated.scheduledFor) ?? 'soon'}`
          : 'Approved',
      )
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Approve failed'
      setFeedback(msg.slice(0, 140))
    } finally {
      setBusy(null)
      setTimeout(() => setFeedback(null), 4000)
    }
  }

  const handleReject = async () => {
    if (typeof window !== 'undefined' && !window.confirm('Reject this draft?')) return
    setBusy('reject')
    setFeedback('Rejecting…')
    try {
      const updated = await rejectLinkedInDraft(draft.id)
      onChange?.(updated)
      setFeedback('Rejected')
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'Reject failed'
      setFeedback(msg.slice(0, 140))
    } finally {
      setBusy(null)
      setTimeout(() => setFeedback(null), 3500)
    }
  }

  const liveBody = editing ? body : draft.body
  const showBodyToggle = liveBody.split('\n').length > BODY_TRUNCATE_LINES || liveBody.length > 240

  const isTerminal = draft.status === 'posted' || draft.status === 'rejected' || draft.status === 'expired'

  return (
    <article
      aria-labelledby={`draft-${draft.id}-hook`}
      className="flex flex-col gap-3 rounded-lg border border-border bg-surface shadow-sm p-4 sm:p-5"
    >
      {/* Header strip */}
      <div className="flex items-center gap-2 flex-wrap text-2xs">
        <Pill tone={angleTone} size="xs">{angleLabel}</Pill>
        <Pill tone={statusTone} size="xs">{statusLabel}</Pill>
        {draft.sourceCompanyName && (
          <span className="uppercase tracking-wide text-fg-subtle font-medium">
            {draft.sourceCompanyName}
          </span>
        )}
        {scheduled && (
          <span className="text-fg-subtle tnum">
            <Icon name="note" size={12} className="inline -mt-0.5 mr-1" />
            {scheduled}
          </span>
        )}
        {draft.whyItWorks && (
          <span
            className="ml-auto text-fg-subtle italic max-w-[55%] sm:max-w-none truncate cursor-help"
            title={draft.whyItWorks}
          >
            <Icon name="info" size={12} className="inline -mt-0.5 mr-1" />
            Why this works
          </span>
        )}
      </div>

      {/* Hook */}
      {editing ? (
        <textarea
          aria-label="Hook"
          value={hook}
          onChange={(e) => setHook(e.target.value)}
          rows={3}
          className="w-full text-sm font-semibold text-fg leading-snug bg-surface-raised rounded-md border border-border focus:border-accent focus:outline-none p-3"
        />
      ) : (
        <h3
          id={`draft-${draft.id}-hook`}
          className="text-sm sm:text-base font-semibold text-fg leading-snug whitespace-pre-line"
        >
          {hook}
        </h3>
      )}

      {/* Body */}
      {editing ? (
        <textarea
          aria-label="Body"
          value={body}
          onChange={(e) => setBody(e.target.value)}
          rows={10}
          className="w-full text-xs sm:text-sm text-fg-muted leading-relaxed bg-surface-raised rounded-md border border-border focus:border-accent focus:outline-none p-3"
        />
      ) : (
        <div
          className={clsx(
            'text-xs sm:text-sm text-fg-muted leading-relaxed whitespace-pre-line',
            !expanded && showBodyToggle && 'line-clamp-3',
          )}
          style={
            !expanded && showBodyToggle
              ? ({
                  display: '-webkit-box',
                  WebkitLineClamp: BODY_TRUNCATE_LINES,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                } as CSSProperties)
              : undefined
          }
        >
          {liveBody}
        </div>
      )}

      {!editing && showBodyToggle && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="self-start text-2xs font-medium text-fg-muted hover:text-fg underline-offset-2 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}

      {/* CTA */}
      {!editing && draft.cta && (
        <p className="text-xs sm:text-sm text-fg italic leading-relaxed">{draft.cta}</p>
      )}

      {/* Hashtags */}
      {draft.hashtags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {draft.hashtags.map((tag) => (
            <span
              key={tag}
              className="inline-flex items-center text-2xs px-2 py-0.5 rounded-md bg-surface-raised text-fg-muted border border-border"
            >
              {tag}
            </span>
          ))}
        </div>
      )}

      {/* Source link */}
      {draft.sourceUrl && (
        <a
          href={draft.sourceUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-2xs text-fg-subtle hover:text-fg underline-offset-2 hover:underline w-fit"
        >
          <Icon name="link" size={12} />
          Source: {draft.sourceCompanyName ?? 'news'}
          <Icon name="arrow-up-right" size={11} />
        </a>
      )}

      {/* Image brief — visual asset recommendation from the G4 image_brief node */}
      {draft.imageBrief && (
        <ImageBriefPanel
          brief={draft.imageBrief}
          onCopy={(label, text) => {
            navigator.clipboard?.writeText(text).then(() => {
              setFeedback(`${label} copied`)
              setTimeout(() => setFeedback(null), 2000)
            }).catch(() => {
              setFeedback(`Couldn't copy ${label.toLowerCase()}`)
              setTimeout(() => setFeedback(null), 2500)
            })
          }}
        />
      )}

      {/* Footer / actions */}
      <div className="flex items-center gap-2 flex-wrap pt-2 border-t border-border mt-auto">
        {feedback && (
          <span aria-live="polite" className="text-2xs text-fg-muted italic">
            {feedback}
          </span>
        )}
        <div className="ml-auto flex items-center gap-2 flex-wrap">
          {!isTerminal && !editing && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setEditing(true)}
              aria-label="Edit draft"
            >
              <Icon name="pencil" size={12} aria-hidden /> Edit
            </Button>
          )}
          {editing && (
            <>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setEditing(false)
                  setHook(draft.hook)
                  setBody(draft.body)
                }}
              >
                Cancel
              </Button>
              <Button
                variant="secondary"
                size="sm"
                onClick={handleEditSave}
                loading={busy === 'save'}
              >
                Save
              </Button>
            </>
          )}
          {!editing && (
            <>
              <Button
                variant="secondary"
                size="sm"
                onClick={handleCopy}
                loading={busy === 'copy'}
              >
                <Icon name="document" size={12} aria-hidden /> Copy
              </Button>
              {draft.status === 'draft' && (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleApprove}
                  loading={busy === 'approve'}
                >
                  <Icon name="check" size={12} aria-hidden /> Approve & Schedule
                </Button>
              )}
              {draft.status === 'draft' && (
                <Button
                  variant="danger"
                  size="sm"
                  onClick={handleReject}
                  loading={busy === 'reject'}
                >
                  <Icon name="x" size={12} aria-hidden /> Reject
                </Button>
              )}
            </>
          )}
        </div>
      </div>
    </article>
  )
}

export default DraftCard


/**
 * ImageBriefPanel — collapsible visual asset brief for a draft.
 *
 * Renders the structured ImageBrief produced by the G4 image_brief node.
 * The engine BRIEFS, never generates — this UI lets the user either:
 *   1. Click the reference URL (preferred when kind = reference_news_image
 *      / screenshot_quote — pull the real source image, no AI tells)
 *   2. Copy the prompt and paste into DALL-E / Imagen / Midjourney
 *   3. Copy the alt text for accessibility on the LinkedIn post
 */
function ImageBriefPanel({
  brief,
  onCopy,
}: {
  brief: ImageBrief
  onCopy: (label: string, text: string) => void
}) {
  // 2026-05-14 fix: was useState(false) — panel started collapsed so users
  // couldn't find the image-generation prompt. Now open by default so the
  // prompt + reference URL + alt text are immediately visible. User can
  // still click the summary to collapse.
  const [open, setOpen] = useState(true)
  const isReference = brief.kind === 'reference_news_image' || brief.recommendedProvider === 'screenshot_only'
  const kindLabel = IMAGE_BRIEF_KIND_LABEL[brief.kind] ?? brief.kind

  return (
    <details
      open={open}
      onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}
      className="rounded-md border border-border bg-surface-raised text-2xs overflow-hidden"
    >
      <summary className="flex items-center gap-2 px-3 py-2 cursor-pointer select-none hover:bg-surface">
        <Icon name="image" size={12} aria-hidden />
        <span className="font-medium text-fg">Visual: {kindLabel}</span>
        <span className="text-fg-subtle">·</span>
        <span className="text-fg-subtle truncate">{brief.rationale || '—'}</span>
        <span className="ml-auto inline-flex items-center gap-1 text-fg-subtle">
          {isReference ? (
            <Pill tone="success" size="sm">
              No AI needed
            </Pill>
          ) : (
            <Pill tone="info" size="sm">
              {brief.recommendedProvider}
            </Pill>
          )}
          {brief.aiDisclosureRecommended && (
            <Pill tone="warning" size="sm" title="LinkedIn best-practice: tag AI-generated images">
              Disclose AI
            </Pill>
          )}
        </span>
      </summary>

      <div className="px-3 pb-3 pt-1 space-y-2">
        {/* Reference URL — preferred path when present */}
        {brief.referenceUrl && (
          <div className="flex items-center gap-2">
            <span className="text-fg-subtle w-20 shrink-0">Reference</span>
            <a
              href={brief.referenceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-fg hover:underline underline-offset-2 truncate"
              title={brief.referenceUrl}
            >
              {brief.referenceUrl}
            </a>
            <button
              type="button"
              onClick={() => onCopy('Reference URL', brief.referenceUrl ?? '')}
              className="ml-auto text-fg-subtle hover:text-fg"
              aria-label="Copy reference URL"
            >
              <Icon name="copy" size={11} />
            </button>
          </div>
        )}

        {/* Image-generation prompt — the core artifact */}
        {brief.prompt && brief.prompt !== '(none — pull the hero image from the source article instead)' && (
          <div className="flex items-start gap-2">
            <span className="text-fg-subtle w-20 shrink-0 pt-0.5">Prompt</span>
            <p className="text-fg whitespace-pre-wrap leading-snug flex-1">{brief.prompt}</p>
            <button
              type="button"
              onClick={() => onCopy('Prompt', brief.prompt)}
              className="text-fg-subtle hover:text-fg shrink-0 pt-0.5"
              aria-label="Copy image prompt"
            >
              <Icon name="copy" size={11} />
            </button>
          </div>
        )}

        {/* Composition notes */}
        {brief.compositionNotes && (
          <div className="flex items-start gap-2">
            <span className="text-fg-subtle w-20 shrink-0 pt-0.5">Composition</span>
            <p className="text-fg-muted whitespace-pre-wrap leading-snug flex-1">
              {brief.compositionNotes}
            </p>
          </div>
        )}

        {/* Data anchors — what the image must illustrate */}
        {brief.dataAnchors.length > 0 && (
          <div className="flex items-start gap-2">
            <span className="text-fg-subtle w-20 shrink-0 pt-0.5">Anchors</span>
            <ul className="list-disc list-inside text-fg-muted leading-snug flex-1">
              {brief.dataAnchors.map((a, i) => (
                <li key={i}>{a}</li>
              ))}
            </ul>
          </div>
        )}

        {/* Alt text — copied to LinkedIn post for accessibility */}
        {brief.altText && (
          <div className="flex items-start gap-2">
            <span className="text-fg-subtle w-20 shrink-0 pt-0.5">Alt text</span>
            <p className="text-fg-muted italic flex-1">{brief.altText}</p>
            <button
              type="button"
              onClick={() => onCopy('Alt text', brief.altText)}
              className="text-fg-subtle hover:text-fg shrink-0 pt-0.5"
              aria-label="Copy alt text"
            >
              <Icon name="copy" size={11} />
            </button>
          </div>
        )}
      </div>
    </details>
  )
}
