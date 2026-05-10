'use client'

/**
 * PrepMaterial — left pane of the Interview Studio.
 *
 * Renders the G3 prep pack as five collapsible sections. Each item gets:
 *   - "Ask tutor about this" → fills the chat input via onAskTutor
 *   - Header has a "Move to advanced" button → calls onEscalate('advanced')
 *
 * Sections:
 *   likely_questions    — numbered list
 *   star_stories        — title + match-quality pill
 *   company_hooks       — one-liners
 *   red_flag_questions  — warning-styled
 *   salary_negotiation  — single body block
 *
 * Uses native <details>/<summary> for "no JS required" collapsing — keeps
 * the prep usable for printing and screen readers.
 */
import { useMemo } from 'react'
import type {
  CompanyHook,
  ConceptLevel,
  InterviewPrep,
  LikelyQuestion,
  PrepSection,
  RedFlagQuestion,
  StarStoryRef,
} from '@/lib/types/interview-studio'
import { Card, Pill, Button } from '@/components/ui'

interface PrepMaterialProps {
  prep: InterviewPrep
  conceptLevel: ConceptLevel
  onAskTutor: (section: PrepSection, item: string) => void
  onEscalate: (target: ConceptLevel) => void
}

interface SectionShellProps {
  title: string
  count?: number
  defaultOpen?: boolean
  conceptLevel: ConceptLevel
  onEscalate: (target: ConceptLevel) => void
  children: React.ReactNode
}

function nextLevel(level: ConceptLevel): ConceptLevel {
  if (level === 'basics') return 'intermediate'
  if (level === 'intermediate') return 'advanced'
  return 'advanced'
}

function SectionShell({
  title,
  count,
  defaultOpen = true,
  conceptLevel,
  onEscalate,
  children,
}: SectionShellProps) {
  const target = nextLevel(conceptLevel)
  const showEscalate = conceptLevel !== 'advanced'
  return (
    <Card padding="md">
      <details open={defaultOpen} className="group">
        <summary className="flex items-center justify-between gap-2 cursor-pointer list-none">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-sm font-semibold text-fg truncate">{title}</span>
            {typeof count === 'number' && (
              <Pill tone="neutral" size="xs">
                {count}
              </Pill>
            )}
          </div>
          {showEscalate && (
            <Button
              size="xs"
              variant="ghost"
              onClick={(e) => {
                e.preventDefault()
                onEscalate(target)
              }}
              aria-label={`Move tutor to ${target}`}
            >
              Move to {target}
            </Button>
          )}
        </summary>
        <div className="mt-3 space-y-2 text-xs text-fg leading-relaxed">{children}</div>
      </details>
    </Card>
  )
}

function asString(value: unknown, ...keys: string[]): string {
  if (typeof value === 'string') return value
  if (value && typeof value === 'object') {
    const obj = value as Record<string, unknown>
    for (const k of keys) {
      const v = obj[k]
      if (typeof v === 'string' && v.trim()) return v
    }
  }
  return ''
}

export default function PrepMaterial({
  prep,
  conceptLevel,
  onAskTutor,
  onEscalate,
}: PrepMaterialProps) {
  const likely = useMemo<LikelyQuestion[]>(
    () => (Array.isArray(prep.likely_questions) ? prep.likely_questions : []),
    [prep.likely_questions],
  )
  const stars = useMemo<StarStoryRef[]>(
    () => (Array.isArray(prep.star_stories) ? prep.star_stories : []),
    [prep.star_stories],
  )
  const hooks = useMemo<CompanyHook[]>(
    () => (Array.isArray(prep.company_hooks) ? prep.company_hooks : []),
    [prep.company_hooks],
  )
  const reds = useMemo<RedFlagQuestion[]>(
    () => (Array.isArray(prep.red_flag_questions) ? prep.red_flag_questions : []),
    [prep.red_flag_questions],
  )

  return (
    <div className="space-y-3">
      <Card padding="sm" tone="muted">
        <div className="flex items-center gap-2 flex-wrap text-2xs text-fg-muted">
          <Pill tone="success" size="xs">
            Pack ready
          </Pill>
          {prep.round_type && (
            <Pill tone="info" size="xs">
              {String(prep.round_type)} round #{prep.round_number ?? 1}
            </Pill>
          )}
          {prep.prep_pack_url && (
            <a
              href={prep.prep_pack_url}
              target="_blank"
              rel="noopener noreferrer"
              className="text-2xs underline hover:text-fg ml-auto"
            >
              Download .md
            </a>
          )}
        </div>
      </Card>

      {/* Likely Questions */}
      <SectionShell
        title="Likely Questions"
        count={likely.length}
        conceptLevel={conceptLevel}
        onEscalate={onEscalate}
      >
        {likely.length === 0 ? (
          <p className="text-2xs text-fg-subtle">No predicted questions in this pack yet.</p>
        ) : (
          <ol className="list-decimal pl-5 space-y-2">
            {likely.map((q, i) => {
              const text = asString(q, 'question', 'text')
              if (!text) return null
              return (
                <li key={i}>
                  <span>{text}</span>
                  <div className="mt-1 flex items-center gap-2 flex-wrap">
                    {q.competency && (
                      <Pill tone="neutral" size="xs">
                        {q.competency}
                      </Pill>
                    )}
                    {q.importance && (
                      <Pill tone="info" size="xs">
                        {q.importance}
                      </Pill>
                    )}
                    <Button
                      size="xs"
                      variant="ghost"
                      onClick={() => onAskTutor('likely_questions', text)}
                    >
                      Ask tutor about this
                    </Button>
                  </div>
                </li>
              )
            })}
          </ol>
        )}
      </SectionShell>

      {/* STAR Stories */}
      <SectionShell
        title="STAR Stories"
        count={stars.length}
        conceptLevel={conceptLevel}
        onEscalate={onEscalate}
      >
        {stars.length === 0 ? (
          <p className="text-2xs text-fg-subtle">
            No matched STAR stories yet. Logging more outcomes will fill this.
          </p>
        ) : (
          <ul className="space-y-2">
            {stars.map((s, i) => {
              const title = asString(s, 'title', 'question')
              if (!title) return null
              return (
                <li key={i} className="flex items-start gap-2">
                  <span className="font-medium">{title}</span>
                  {s.match_quality && (
                    <Pill
                      tone={
                        s.match_quality === 'strong'
                          ? 'success'
                          : s.match_quality === 'partial'
                            ? 'warning'
                            : 'neutral'
                      }
                      size="xs"
                    >
                      {s.match_quality}
                    </Pill>
                  )}
                  <Button
                    size="xs"
                    variant="ghost"
                    className="ml-auto"
                    onClick={() => onAskTutor('star_stories', title)}
                  >
                    Ask tutor
                  </Button>
                </li>
              )
            })}
          </ul>
        )}
      </SectionShell>

      {/* Company Hooks */}
      <SectionShell
        title="Company Hooks"
        count={hooks.length}
        defaultOpen={false}
        conceptLevel={conceptLevel}
        onEscalate={onEscalate}
      >
        {hooks.length === 0 ? (
          <p className="text-2xs text-fg-subtle">No company hooks captured yet.</p>
        ) : (
          <ul className="space-y-2">
            {hooks.map((h, i) => {
              const text =
                typeof h === 'string' ? h : asString(h, 'hook')
              if (!text) return null
              return (
                <li key={i} className="flex items-start gap-2">
                  <span>{text}</span>
                  <Button
                    size="xs"
                    variant="ghost"
                    className="ml-auto shrink-0"
                    onClick={() => onAskTutor('company_hooks', text)}
                  >
                    Ask tutor
                  </Button>
                </li>
              )
            })}
          </ul>
        )}
      </SectionShell>

      {/* Red-Flag Questions */}
      <SectionShell
        title="Red-Flag Questions (for you to ask THEM)"
        count={reds.length}
        defaultOpen={false}
        conceptLevel={conceptLevel}
        onEscalate={onEscalate}
      >
        {reds.length === 0 ? (
          <p className="text-2xs text-fg-subtle">No red-flag questions captured.</p>
        ) : (
          <ul className="space-y-2">
            {reds.map((r, i) => {
              const text =
                typeof r === 'string' ? r : asString(r, 'question')
              if (!text) return null
              return (
                <li key={i} className="flex items-start gap-2">
                  <Pill tone="warning" size="xs">
                    !
                  </Pill>
                  <span>{text}</span>
                  <Button
                    size="xs"
                    variant="ghost"
                    className="ml-auto shrink-0"
                    onClick={() => onAskTutor('red_flag_questions', text)}
                  >
                    Ask tutor
                  </Button>
                </li>
              )
            })}
          </ul>
        )}
      </SectionShell>

      {/* Salary Negotiation */}
      <SectionShell
        title="Salary Negotiation"
        defaultOpen={false}
        conceptLevel={conceptLevel}
        onEscalate={onEscalate}
      >
        {prep.salary_negotiation_notes ? (
          <>
            <p className="whitespace-pre-line">{prep.salary_negotiation_notes}</p>
            <div className="mt-2">
              <Button
                size="xs"
                variant="ghost"
                onClick={() =>
                  onAskTutor(
                    'salary_negotiation',
                    'Walk me through the salary negotiation strategy in this pack.',
                  )
                }
              >
                Ask tutor about salary
              </Button>
            </div>
          </>
        ) : (
          <p className="text-2xs text-fg-subtle">No salary notes captured yet.</p>
        )}
      </SectionShell>
    </div>
  )
}
