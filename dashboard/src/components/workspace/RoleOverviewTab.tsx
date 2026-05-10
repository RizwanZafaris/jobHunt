/**
 * RoleOverviewTab — the "what is this role and why does it fit me" view.
 *
 * Composition:
 *   • Job description (markdown rendered inline — same prose styling as
 *     ResumeWorkspace's <ResumePreview>).
 *   • Fit details — matched skills + gaps + tailoring notes from the
 *     G1/G2 pipeline's `jobs.fit_details` jsonb.
 *   • "Why you fit" — derived client-side as the intersection of the
 *     persona's ATS keyword bank (required ∪ boost) with the JD text.
 *     This is the spec's load-bearing computation.
 *
 * Why we compute "why you fit" here, not in api/workspace.py:
 *   The persona and the JD are both already in the bundle. Rendering
 *   the intersection is cheap on the client, keeps the API endpoint
 *   pure (no string-mangling), and lets the UI tweak the matching
 *   logic (case-insensitive, word-boundary, multi-word phrases) without
 *   a backend deploy. If the persona moves to LLM-extracted "why this
 *   matches" reasons, that's a backend job — but for V1 the
 *   intersection is honest signal.
 */
'use client'

import { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Card } from '@/components/ui/Card'
import { Pill } from '@/components/ui/Pill'
import { Icon } from '@/components/ui/Icon'
import { EmptyState } from '@/components/ui/EmptyState'
import type {
  WorkspaceJob,
  WorkspacePersona,
  WorkspaceFitDetails,
} from '@/lib/types/workspace'

const PROSE_CLS =
  'text-fg text-sm leading-relaxed ' +
  '[&_h1]:text-xl [&_h1]:font-semibold [&_h1]:mt-0 [&_h1]:mb-2 [&_h1]:text-fg ' +
  '[&_h2]:text-base [&_h2]:font-semibold [&_h2]:mt-5 [&_h2]:mb-2 [&_h2]:text-fg ' +
  '[&_h3]:text-sm [&_h3]:font-semibold [&_h3]:mt-3 [&_h3]:mb-1.5 [&_h3]:text-fg ' +
  '[&_p]:my-2 [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:my-2 [&_li]:my-0.5 ' +
  '[&_strong]:font-semibold [&_strong]:text-fg ' +
  '[&_a]:text-accent [&_a:hover]:underline ' +
  '[&_hr]:border-border [&_hr]:my-4 ' +
  '[&_code]:bg-surface-raised [&_code]:px-1 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-2xs'

export interface RoleOverviewTabProps {
  job: WorkspaceJob
  persona: WorkspacePersona | null
}

export function RoleOverviewTab({ job, persona }: RoleOverviewTabProps) {
  const fit: WorkspaceFitDetails = job.fit_details ?? {}
  const matchedSkills = Array.isArray(fit.matched_skills) ? fit.matched_skills.filter(Boolean) : []
  const gaps = Array.isArray(fit.gaps) ? fit.gaps.filter(Boolean) : []
  const tailoringNotes = typeof fit.tailoring_notes === 'string' ? fit.tailoring_notes.trim() : ''
  const explicitWhyFit = Array.isArray(fit.why_you_fit)
    ? fit.why_you_fit.filter((s): s is string => typeof s === 'string' && s.trim().length > 0)
    : []

  const whyYouFit = useMemo(
    () => deriveWhyYouFit(explicitWhyFit, persona, job.description ?? ''),
    [explicitWhyFit, persona, job.description],
  )

  const description = (job.description ?? '').trim()

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
      {/* Main column: description + tailoring */}
      <div className="lg:col-span-2 space-y-4">
        <Card
          padding="md"
          title="Role brief"
          description={
            job.archetype
              ? `Archetype: ${job.archetype}${job.location ? ` · ${job.location}` : ''}`
              : job.location ?? undefined
          }
        >
          {description ? (
            <article className={PROSE_CLS}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{description}</ReactMarkdown>
            </article>
          ) : (
            <EmptyState
              icon="document"
              title="No description on file"
              description="The discovery pass didn't capture a JD body for this posting. Re-run the scout to refresh."
            />
          )}
        </Card>

        {tailoringNotes && (
          <Card
            padding="md"
            title="Tailoring notes"
            description="What G2 used to bias the resume toward this role"
          >
            <article className={PROSE_CLS}>
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{tailoringNotes}</ReactMarkdown>
            </article>
          </Card>
        )}
      </div>

      {/* Side column: fit + why-you-fit */}
      <div className="space-y-4">
        <Card
          padding="md"
          title="Why you fit"
          description={
            whyYouFit.source === 'persona_intersection'
              ? 'ATS keywords from the persona that also appear in this JD'
              : whyYouFit.source === 'pipeline'
              ? 'Reasons recorded by the discovery pipeline'
              : undefined
          }
        >
          {whyYouFit.items.length === 0 ? (
            <p className="text-2xs text-fg-subtle leading-relaxed">
              No persona-grounded matches yet. Build a persona for{' '}
              <span className="text-fg-muted font-medium">{job.company}</span>{' '}
              to surface the intersection.
            </p>
          ) : (
            <ul className="flex flex-wrap gap-1.5" aria-label="Why you fit">
              {whyYouFit.items.map((item) => (
                <li key={item}>
                  <Pill
                    tone={whyYouFit.source === 'persona_intersection' ? 'success' : 'info'}
                    size="xs"
                    icon={<Icon name="check" size={10} />}
                  >
                    {item}
                  </Pill>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          padding="md"
          title="Matched skills"
          description={matchedSkills.length === 0 ? 'No matched skills recorded' : undefined}
        >
          {matchedSkills.length > 0 && (
            <ul className="flex flex-wrap gap-1.5" aria-label="Matched skills">
              {matchedSkills.map((skill) => (
                <li key={skill}>
                  <Pill tone="info" size="xs">
                    {skill}
                  </Pill>
                </li>
              ))}
            </ul>
          )}
        </Card>

        {gaps.length > 0 && (
          <Card
            padding="md"
            title="Gaps to address"
            description="Areas to compensate for in the resume or cover note"
          >
            <ul className="flex flex-wrap gap-1.5" aria-label="Gaps">
              {gaps.map((gap) => (
                <li key={gap}>
                  <Pill tone="warning" size="xs">
                    {gap}
                  </Pill>
                </li>
              ))}
            </ul>
          </Card>
        )}

        {persona && (
          <Card
            padding="md"
            title="Persona snapshot"
            description={
              persona.persona_version
                ? `v${persona.persona_version}${persona.last_synthesized_at ? ` · synthesised ${formatDate(persona.last_synthesized_at)}` : ''}`
                : undefined
            }
          >
            <PersonaSummary persona={persona} />
          </Card>
        )}
      </div>
    </div>
  )
}

// ── Persona snapshot pill grid ────────────────────────────────────────────
function PersonaSummary({ persona }: { persona: WorkspacePersona }) {
  const bank = persona.ats_keyword_bank ?? {}
  const required = (bank.required ?? []).slice(0, 6)
  const boost = (bank.boost ?? []).slice(0, 6)
  const banned = (bank.banned ?? []).slice(0, 4)

  if (required.length + boost.length + banned.length === 0) {
    return (
      <p className="text-2xs text-fg-subtle leading-relaxed">
        ATS keyword bank not yet populated for this persona.
      </p>
    )
  }

  return (
    <div className="space-y-2">
      {required.length > 0 && (
        <div>
          <p className="text-3xs uppercase tracking-wider text-fg-subtle font-semibold mb-1">
            Required
          </p>
          <ul className="flex flex-wrap gap-1">
            {required.map((k) => (
              <li key={`req-${k}`}>
                <Pill tone="accent" size="xs">{k}</Pill>
              </li>
            ))}
          </ul>
        </div>
      )}
      {boost.length > 0 && (
        <div>
          <p className="text-3xs uppercase tracking-wider text-fg-subtle font-semibold mb-1">
            Boost
          </p>
          <ul className="flex flex-wrap gap-1">
            {boost.map((k) => (
              <li key={`boost-${k}`}>
                <Pill tone="info" size="xs">{k}</Pill>
              </li>
            ))}
          </ul>
        </div>
      )}
      {banned.length > 0 && (
        <div>
          <p className="text-3xs uppercase tracking-wider text-fg-subtle font-semibold mb-1">
            Banned
          </p>
          <ul className="flex flex-wrap gap-1">
            {banned.map((k) => (
              <li key={`banned-${k}`}>
                <Pill tone="danger" size="xs">{k}</Pill>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}

// ── "Why you fit" derivation ──────────────────────────────────────────────
type WhyYouFitSource = 'pipeline' | 'persona_intersection' | 'empty'

interface WhyYouFitResult {
  items: string[]
  source: WhyYouFitSource
}

/**
 * Decide what "why you fit" should display.
 *
 * Order of preference:
 *   1. The G1/G2 pipeline's explicit `fit_details.why_you_fit` if present.
 *      That takes precedence — the LLM has more nuance than a string match.
 *   2. Otherwise compute the intersection of the persona's ATS keyword
 *      bank (required ∪ boost) with the JD text. Case-insensitive, with
 *      whole-word boundaries so "PM" doesn't match "PMP" and "iOS"
 *      doesn't match "ios" inside "previous".
 *
 * We dedupe + cap at 12 items so the chip cluster doesn't dominate the
 * sidebar.
 */
function deriveWhyYouFit(
  explicit: string[],
  persona: WorkspacePersona | null,
  jdText: string,
): WhyYouFitResult {
  if (explicit.length > 0) {
    return { items: explicit.slice(0, 12), source: 'pipeline' }
  }
  if (!persona || !jdText) {
    return { items: [], source: 'empty' }
  }
  const bank = persona.ats_keyword_bank ?? {}
  // Dedupe via a record (works under es5 target — no Set iteration).
  const seen: Record<string, true> = {}
  const candidates: string[] = []
  for (const k of bank.required ?? []) {
    if (typeof k === 'string') {
      const t = k.trim()
      if (t && !seen[t]) {
        seen[t] = true
        candidates.push(t)
      }
    }
  }
  for (const k of bank.boost ?? []) {
    if (typeof k === 'string') {
      const t = k.trim()
      if (t && !seen[t]) {
        seen[t] = true
        candidates.push(t)
      }
    }
  }
  if (candidates.length === 0) {
    return { items: [], source: 'empty' }
  }
  const haystack = jdText.toLowerCase()
  const hits: string[] = []
  for (const k of candidates) {
    if (matchesWordBoundary(haystack, k.toLowerCase())) {
      hits.push(k)
    }
  }
  return {
    items: hits.slice(0, 12),
    source: hits.length > 0 ? 'persona_intersection' : 'empty',
  }
}

/**
 * Word-boundary match for short ATS phrases. Avoids the false positive
 * "iOS" → "previous" ("ios" inside the word). Uses a simple regex
 * escape because the keyword bank may include `+` ("C++") or `.` (".net").
 */
function matchesWordBoundary(haystack: string, needle: string): boolean {
  if (!needle) return false
  const escaped = needle.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  // \b doesn't fire around non-word characters like '+' or '#'. We use a
  // looser boundary: start-of-string OR non-alphanumeric-or-hyphen on
  // either side.
  const re = new RegExp(`(^|[^a-z0-9-])${escaped}($|[^a-z0-9-])`, 'i')
  return re.test(haystack)
}

function formatDate(iso?: string | null): string {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString('en-GB', {
      day: 'numeric',
      month: 'short',
      year: 'numeric',
    })
  } catch {
    return iso.slice(0, 10)
  }
}

export default RoleOverviewTab
