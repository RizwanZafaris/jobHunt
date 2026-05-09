import { fetchJobDetail } from '@/lib/profile-api'
import JobApplyButton from '@/components/JobApplyButton'
import GenerateResumeButton from '@/components/GenerateResumeButton'
import OutcomeLogger from '@/components/OutcomeLogger'
import { AppShell } from '@/components/layout/AppShell'
import { PageHeader } from '@/components/ui/PageHeader'
import { Card } from '@/components/ui/Card'
import { EmptyState } from '@/components/ui/EmptyState'
import { Pill, type PillTone } from '@/components/ui/Pill'
import { Icon } from '@/components/ui/Icon'

export const dynamic = 'force-dynamic'

const SCORE_TONE = (s: number): PillTone =>
  s >= 80 ? 'success' : s >= 65 ? 'info' : s >= 50 ? 'warning' : 'neutral'

const LEGITIMACY_TONE = (tier: string | undefined | null): PillTone =>
  tier === 'High Confidence' ? 'success'
  : tier === 'Suspicious' ? 'danger'
  : 'warning'

interface FitDetails {
  gpt4_reason?: string
  gaps_found?: number
  gaps_filled?: number
  strengths?: string[]
  tailoring_priorities?: string[]
}

interface EvaluationBlocks {
  block_a_role_summary?: { tldr?: string; level_signal?: string; key_themes?: string[] }
  block_b_match?: {
    matched_requirements?: Array<{ jd_requirement?: string; candidate_evidence?: string }>
    gaps?: Array<{ requirement?: string; severity?: string; mitigation?: string }>
  }
  block_c_level_strategy?: { target_level?: string; selling_phrases?: string[] }
  block_e_personalization?: { summary_rewrite?: string; ats_keywords_to_inject?: string[] }
  block_f_interview_prep?: {
    likely_questions_with_star?: Array<{ question?: string; star_situation?: string; action?: string; result?: string }>
  }
  tailoring_priorities?: string[]
}

interface Job {
  id: number
  title: string
  company: string
  location: string | null
  match_score: number
  status: string
  url: string | null
  source?: string | null
  description?: string | null
  archetype?: string | null
  legitimacy_tier?: string | null
  legitimacy_signals?: string[] | null
  fit_details?: FitDetails | null
  evaluation_blocks?: EvaluationBlocks | null
  resume_path?: string | null
  email_path?: string | null
  interview_path?: string | null
  resume_generated_at?: string | null
}

interface Artifact {
  exists?: boolean
  size?: number
  content?: string
  url?: string
  kind?: string
}

interface JobDetailData {
  job: Job
  application?: { id?: string | null } | null
  artifacts: { resume_path?: Artifact; email_path?: Artifact; interview_path?: Artifact }
}

export default async function JobDetailPage({ params }: { params: { id: string } }) {
  let data: JobDetailData | undefined
  let error: string | null = null
  try {
    data = (await fetchJobDetail(params.id)) as JobDetailData
  } catch (e: unknown) {
    error = e instanceof Error ? e.message : 'Failed to load job'
  }

  if (error || !data) {
    return (
      <AppShell>
        <EmptyState
          icon="alert-triangle"
          title="Job not found"
          description={error ?? undefined}
        />
      </AppShell>
    )
  }

  const j = data.job
  const fit = j.fit_details || {}

  return (
    <AppShell>
      <PageHeader
        eyebrow={
          <span className="flex items-center gap-1.5">
            <Icon name="briefcase" size={11} />
            {j.company}{j.location ? ` · ${j.location}` : ''}
          </span>
        }
        title={j.title}
        actions={
          <div className="flex flex-col items-end gap-2">
            <GenerateResumeButton
              jobId={j.id}
              score={j.match_score}
              alreadyGenerated={Boolean(j.resume_generated_at)}
              archetype={j.archetype}
            />
            <JobApplyButton jobId={j.id} existingApp={data.application ?? undefined} />
          </div>
        }
      />

      <div className="flex flex-wrap gap-1.5 -mt-3">
        <Pill tone={SCORE_TONE(j.match_score)}>Match {j.match_score}/100</Pill>
        {j.archetype && <Pill tone="success">{j.archetype}</Pill>}
        {j.legitimacy_tier && (
          <Pill
            tone={LEGITIMACY_TONE(j.legitimacy_tier)}
            title={(j.legitimacy_signals || []).join(' · ')}
          >
            {j.legitimacy_tier}
          </Pill>
        )}
        <Pill>{j.status}</Pill>
        {j.source && <Pill>via {j.source}</Pill>}
        {j.url && (
          <a
            href={j.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1 text-2xs text-info hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
          >
            Original posting <Icon name="arrow-up-right" size={11} />
          </a>
        )}
      </div>

      {(j.legitimacy_signals?.length ?? 0) > 0 && (
        <p className="text-2xs text-fg-subtle">
          <span className="font-semibold text-fg-muted">Legitimacy signals:</span>{' '}
          {(j.legitimacy_signals || []).join(' · ')}
        </p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <div className="lg:col-span-2 space-y-4">
          {fit && Object.keys(fit).length > 0 && (
            <Card title="Fit analysis">
              {fit.gpt4_reason && (
                <p className="text-xs text-fg-muted italic mb-3 leading-relaxed">&ldquo;{fit.gpt4_reason}&rdquo;</p>
              )}
              <dl className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-xs mb-3">
                {fit.gaps_found !== undefined && <Stat label="Gaps found" value={fit.gaps_found} />}
                {fit.gaps_filled !== undefined && <Stat label="Gaps filled" value={fit.gaps_filled} />}
              </dl>
              {fit.strengths && fit.strengths.length > 0 && (
                <div className="mb-3">
                  <h3 className="text-2xs uppercase tracking-wider text-fg-subtle mb-1.5 font-semibold">Strengths</h3>
                  <div className="flex flex-wrap gap-1">
                    {fit.strengths.map((s, i) => (
                      <Pill key={i} tone="success">{s}</Pill>
                    ))}
                  </div>
                </div>
              )}
              {fit.tailoring_priorities && fit.tailoring_priorities.length > 0 && (
                <div>
                  <h3 className="text-2xs uppercase tracking-wider text-fg-subtle mb-1.5 font-semibold">Tailoring priorities</h3>
                  <ul className="text-xs text-fg-muted space-y-1">
                    {fit.tailoring_priorities.map((p, i) => (
                      <li key={i} className="flex gap-2">
                        <span aria-hidden className="text-fg-subtle">•</span>
                        <span>{p}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </Card>
          )}

          {j.evaluation_blocks && Object.keys(j.evaluation_blocks).length > 0 && (
            <Card title="Recruitment-expert brief" tone="success">
              <EvaluationBlocksView blocks={j.evaluation_blocks} />
            </Card>
          )}

          {j.description && (
            <Card title="Job description">
              <div className="text-xs text-fg-muted leading-relaxed whitespace-pre-wrap font-sans">
                {j.description}
              </div>
            </Card>
          )}
        </div>

        <div className="space-y-3">
          <ArtifactCard title="Tailored resume" icon="document" path={j.resume_path ?? null} artifact={data.artifacts.resume_path} />
          <ArtifactCard title="Cover email" icon="mail" path={j.email_path ?? null} artifact={data.artifacts.email_path} preview />
          <ArtifactCard title="Interview prep" icon="target" path={j.interview_path ?? null} artifact={data.artifacts.interview_path} preview />
          <OutcomeLogger
            jobId={j.id}
            applicationId={data.application?.id || null}
            companyName={j.company}
          />
        </div>
      </div>
    </AppShell>
  )
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-surface-raised border border-border rounded-md px-3 py-2">
      <dt className="text-2xs text-fg-subtle uppercase tracking-wider">{label}</dt>
      <dd className="text-base font-semibold text-fg tnum mt-0.5">{value}</dd>
    </div>
  )
}

function EvaluationBlocksView({ blocks }: { blocks: EvaluationBlocks }) {
  return (
    <div className="space-y-4">
      {blocks.block_a_role_summary && (
        <Section title="A · Role summary">
          <div className="space-y-1.5 text-xs text-fg-muted">
            {blocks.block_a_role_summary.tldr && <p className="italic">&ldquo;{blocks.block_a_role_summary.tldr}&rdquo;</p>}
            {blocks.block_a_role_summary.level_signal && (
              <p><span className="text-fg-subtle">Level:</span> {blocks.block_a_role_summary.level_signal}</p>
            )}
            {(blocks.block_a_role_summary.key_themes?.length ?? 0) > 0 && (
              <div className="flex flex-wrap gap-1">
                {blocks.block_a_role_summary.key_themes!.map((t, i) => (
                  <Pill key={i}>{t}</Pill>
                ))}
              </div>
            )}
          </div>
        </Section>
      )}

      {blocks.block_b_match && (
        <Section title="B · Match & gaps">
          <div className="space-y-2.5">
            {(blocks.block_b_match.matched_requirements?.length ?? 0) > 0 && (
              <div>
                <div className="text-2xs uppercase text-fg-subtle mb-1 font-semibold">Matched</div>
                <ul className="text-xs text-fg-muted space-y-1">
                  {blocks.block_b_match.matched_requirements!.slice(0, 5).map((m, i) => (
                    <li key={i} className="flex gap-2">
                      <Icon name="check" size={12} className="text-success mt-0.5 shrink-0" />
                      <span><span className="text-success">{m.jd_requirement}</span> — {m.candidate_evidence}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {(blocks.block_b_match.gaps?.length ?? 0) > 0 && (
              <div>
                <div className="text-2xs uppercase text-fg-subtle mb-1 font-semibold">Gaps</div>
                <ul className="text-xs text-fg-muted space-y-1">
                  {blocks.block_b_match.gaps!.slice(0, 5).map((g, i) => {
                    const tone = g.severity === 'blocker' ? 'text-danger' : g.severity === 'gap' ? 'text-warning' : 'text-fg-subtle'
                    const symbol = g.severity === 'blocker' ? '!' : g.severity === 'gap' ? '?' : '·'
                    return (
                      <li key={i} className="flex gap-2">
                        <span aria-label={g.severity || 'note'} className={`${tone} font-bold tabular-nums w-4 text-center`}>{symbol}</span>
                        <span>{g.requirement} <span className="text-fg-subtle">— {g.mitigation}</span></span>
                      </li>
                    )
                  })}
                </ul>
              </div>
            )}
          </div>
        </Section>
      )}

      {blocks.block_c_level_strategy && (
        <Section title="C · Level strategy">
          <div className="space-y-1.5 text-xs text-fg-muted">
            {blocks.block_c_level_strategy.target_level && (
              <p><span className="text-fg-subtle">Target:</span> {blocks.block_c_level_strategy.target_level}</p>
            )}
            {(blocks.block_c_level_strategy.selling_phrases?.length ?? 0) > 0 && (
              <ul className="space-y-1">
                {blocks.block_c_level_strategy.selling_phrases!.map((p, i) => (
                  <li key={i} className="flex gap-2">
                    <span aria-hidden className="text-fg-subtle">•</span>
                    <span className="italic">&ldquo;{p}&rdquo;</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </Section>
      )}

      {blocks.block_e_personalization && (
        <Section title="E · Tailoring">
          <div className="space-y-2">
            {blocks.block_e_personalization.summary_rewrite && (
              <div className="bg-surface-raised border border-border rounded-md p-2.5">
                <div className="text-2xs uppercase text-fg-subtle mb-1 font-semibold">Proposed summary</div>
                <p className="text-xs text-fg-muted italic">{blocks.block_e_personalization.summary_rewrite}</p>
              </div>
            )}
            {(blocks.block_e_personalization.ats_keywords_to_inject?.length ?? 0) > 0 && (
              <div className="flex flex-wrap gap-1">
                {blocks.block_e_personalization.ats_keywords_to_inject!.map((k, i) => (
                  <Pill key={i} tone="info">{k}</Pill>
                ))}
              </div>
            )}
          </div>
        </Section>
      )}

      {blocks.block_f_interview_prep && (
        <Section title="F · Interview prep">
          <div className="space-y-2">
            {(blocks.block_f_interview_prep.likely_questions_with_star?.length ?? 0) > 0 && (
              blocks.block_f_interview_prep.likely_questions_with_star!.slice(0, 3).map((q, i) => (
                <div key={i} className="bg-surface-raised border border-border rounded-md p-2.5 text-xs text-fg-muted">
                  <p className="font-semibold text-fg">Q: {q.question}</p>
                  {q.star_situation && <p className="mt-1"><span className="text-fg-subtle">S:</span> {q.star_situation}</p>}
                  {q.action && <p><span className="text-fg-subtle">A:</span> {q.action}</p>}
                  {q.result && <p><span className="text-fg-subtle">R:</span> {q.result}</p>}
                </div>
              ))
            )}
          </div>
        </Section>
      )}

      {(blocks.tailoring_priorities?.length ?? 0) > 0 && (
        <Section title="Top resume changes (priority order)">
          <ol className="text-xs text-fg-muted space-y-1 list-decimal list-outside ml-4">
            {blocks.tailoring_priorities!.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ol>
        </Section>
      )}
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div>
      <h3 className="text-2xs font-semibold text-success uppercase tracking-wider mb-2">{title}</h3>
      {children}
    </div>
  )
}

function ArtifactCard({
  title,
  icon,
  path,
  artifact,
  preview,
}: {
  title: string
  icon: 'document' | 'mail' | 'target'
  path: string | null
  artifact?: Artifact
  preview?: boolean
}) {
  const exists = artifact?.exists
  const kind = artifact?.kind
  return (
    <Card
      title={
        <span className="flex items-center gap-1.5">
          <Icon name={icon} size={14} className="text-fg-muted" />
          {title}
        </span>
      }
    >
      {!path ? (
        <p className="text-2xs text-fg-subtle italic">Not generated yet — use &ldquo;Generate resume&rdquo; above.</p>
      ) : !exists ? (
        <p className="text-2xs text-warning">
          Generated, but artifact lost on redeploy. Click &ldquo;Re-generate resume&rdquo; to rebuild.
        </p>
      ) : kind === 'remote' && artifact?.url ? (
        <>
          <a
            href={artifact.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-2xs bg-accent text-accent-fg hover:bg-accent-hover px-3 py-2 rounded-md font-medium min-h-9 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors"
          >
            <Icon name="download" size={12} />
            Download
          </a>
          <p className="text-2xs text-fg-subtle mt-2 break-all font-mono">{path.split('/').pop()}</p>
        </>
      ) : (
        <>
          <p className="text-2xs text-fg-subtle break-all font-mono">{path.split('/').pop()}</p>
          {artifact?.size !== undefined && (
            <p className="text-2xs text-fg-subtle">{(artifact.size / 1024).toFixed(1)} KB</p>
          )}
          {preview && artifact?.content && (
            <pre className="mt-2 text-2xs text-fg-muted leading-relaxed whitespace-pre-wrap font-mono bg-surface-raised border border-border rounded p-2 max-h-64 overflow-y-auto">
              {artifact.content.slice(0, 2000)}
              {artifact.content.length > 2000 ? '\n\n... (truncated)' : ''}
            </pre>
          )}
        </>
      )}
    </Card>
  )
}
