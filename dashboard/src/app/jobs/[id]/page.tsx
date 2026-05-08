import { fetchJobDetail } from '@/lib/profile-api'
import ProfileNav from '@/components/ProfileNav'
import JobApplyButton from '@/components/JobApplyButton'
import GenerateResumeButton from '@/components/GenerateResumeButton'

export const dynamic = 'force-dynamic'

const SCORE_COLOR = (s: number) =>
  s >= 80 ? 'bg-emerald-900/40 border-emerald-800 text-emerald-300'
  : s >= 65 ? 'bg-blue-900/40 border-blue-800 text-blue-300'
  : s >= 50 ? 'bg-amber-900/40 border-amber-800 text-amber-300'
  : 'bg-gray-800 border-gray-700 text-gray-400'

export default async function JobDetailPage({ params }: { params: { id: string } }) {
  let data
  let error: string | null = null
  try {
    data = await fetchJobDetail(params.id)
  } catch (e: any) {
    error = e?.message || 'Failed to load job'
  }

  if (error || !data) {
    return (
      <Shell>
        <div className="bg-gray-900 border border-gray-800 rounded-xl p-8 text-center">
          <h2 className="text-lg font-semibold text-white">Job not found</h2>
          {error && <p className="text-sm text-gray-400 mt-2">{error}</p>}
        </div>
      </Shell>
    )
  }

  const j = data.job
  const fit = j.fit_details || {}
  return (
    <Shell>
      {/* Header */}
      <section className="bg-gradient-to-br from-gray-900 to-gray-950 border border-gray-800 rounded-2xl p-6">
        <div className="flex items-start justify-between gap-4 flex-wrap">
          <div className="flex-1 min-w-0">
            <h1 className="text-2xl font-bold text-white">{j.title}</h1>
            <p className="text-base text-blue-400 mt-1">
              {j.company} {j.location && <span className="text-gray-500">· {j.location}</span>}
            </p>
            <div className="flex flex-wrap gap-2 mt-3">
              <span className={`text-xs border px-2 py-0.5 rounded ${SCORE_COLOR(j.match_score)}`}>
                Match {j.match_score}/100
              </span>
              {j.archetype && (
                <span className="text-xs bg-emerald-900/40 border border-emerald-800 text-emerald-300 px-2 py-0.5 rounded">
                  📐 {j.archetype}
                </span>
              )}
              {j.legitimacy_tier && (
                <span
                  className={`text-xs border px-2 py-0.5 rounded ${
                    j.legitimacy_tier === 'High Confidence'
                      ? 'bg-emerald-900/40 border-emerald-800 text-emerald-300'
                      : j.legitimacy_tier === 'Suspicious'
                      ? 'bg-red-900/40 border-red-800 text-red-300'
                      : 'bg-amber-900/40 border-amber-800 text-amber-300'
                  }`}
                  title={(j.legitimacy_signals || []).join(' · ')}
                >
                  {j.legitimacy_tier === 'High Confidence' ? '✅' : j.legitimacy_tier === 'Suspicious' ? '⚠️' : '◑'}{' '}
                  {j.legitimacy_tier}
                </span>
              )}
              <span className="text-xs bg-gray-800 border border-gray-700 text-gray-300 px-2 py-0.5 rounded">
                {j.status}
              </span>
              {j.source && (
                <span className="text-xs bg-gray-800 border border-gray-700 text-gray-400 px-2 py-0.5 rounded">
                  via {j.source}
                </span>
              )}
              {j.url && (
                <a
                  href={j.url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-blue-400 hover:text-blue-300 underline"
                >
                  Original posting ↗
                </a>
              )}
            </div>
            {(j.legitimacy_signals?.length ?? 0) > 0 && (
              <div className="mt-3 text-[11px] text-gray-500">
                <span className="font-semibold">Legitimacy signals:</span>{' '}
                {(j.legitimacy_signals || []).join(' · ')}
              </div>
            )}
          </div>
          <div className="flex flex-col items-end gap-3">
            <GenerateResumeButton
              jobId={j.id}
              score={j.match_score}
              alreadyGenerated={Boolean(j.resume_generated_at)}
              archetype={j.archetype}
            />
            <JobApplyButton jobId={j.id} existingApp={data.application} />
          </div>
        </div>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Left col: fit + JD */}
        <div className="lg:col-span-2 space-y-6">
          {/* Fit details */}
          {fit && (Object.keys(fit).length > 0) && (
            <section className="bg-gray-900 border border-gray-800 rounded-xl p-5">
              <h2 className="text-sm font-semibold text-white uppercase tracking-wider mb-3">
                Fit Analysis
              </h2>
              {fit.gpt4_reason && (
                <p className="text-xs text-gray-400 italic mb-3">"{fit.gpt4_reason}"</p>
              )}
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-xs mb-3">
                {fit.gaps_found !== undefined && (
                  <Stat label="Gaps found" value={fit.gaps_found} />
                )}
                {fit.gaps_filled !== undefined && (
                  <Stat label="Gaps filled" value={fit.gaps_filled} />
                )}
              </div>
              {fit.strengths && fit.strengths.length > 0 && (
                <div className="mb-3">
                  <h3 className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5">
                    Strengths
                  </h3>
                  <div className="flex flex-wrap gap-1.5">
                    {fit.strengths.map((s: string, i: number) => (
                      <span
                        key={i}
                        className="text-[11px] bg-emerald-900/30 border border-emerald-800 text-emerald-300 px-2 py-0.5 rounded"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {fit.tailoring_priorities && fit.tailoring_priorities.length > 0 && (
                <div>
                  <h3 className="text-[10px] uppercase tracking-wider text-gray-500 mb-1.5">
                    Tailoring priorities
                  </h3>
                  <ul className="text-xs text-gray-300 space-y-1">
                    {fit.tailoring_priorities.map((p: string, i: number) => (
                      <li key={i}>• {p}</li>
                    ))}
                  </ul>
                </div>
              )}
            </section>
          )}

          {/* A-F Evaluation Blocks (workflow v2) */}
          {j.evaluation_blocks && Object.keys(j.evaluation_blocks).length > 0 && (
            <section className="bg-gray-900 border border-emerald-900/50 rounded-xl p-5">
              <h2 className="text-sm font-semibold text-emerald-300 uppercase tracking-wider mb-3">
                Recruitment-Expert Brief
              </h2>
              <EvaluationBlocks blocks={j.evaluation_blocks} />
            </section>
          )}

          {/* JD */}
          {j.description && (
            <section className="bg-gray-900 border border-gray-800 rounded-xl p-5">
              <h2 className="text-sm font-semibold text-white uppercase tracking-wider mb-3">
                Job Description
              </h2>
              <pre className="text-xs text-gray-300 leading-relaxed whitespace-pre-wrap font-sans">
                {j.description}
              </pre>
            </section>
          )}
        </div>

        {/* Right col: artifacts */}
        <div className="space-y-4">
          <ArtifactCard
            title="📄 Tailored Resume"
            path={j.resume_path}
            artifact={data.artifacts.resume_path}
          />
          <ArtifactCard
            title="✉ Cover Email"
            path={j.email_path}
            artifact={data.artifacts.email_path}
            preview
          />
          <ArtifactCard
            title="🎯 Interview Prep"
            path={j.interview_path}
            artifact={data.artifacts.interview_path}
            preview
          />
        </div>
      </div>
    </Shell>
  )
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-200">
      <header className="border-b border-gray-800 bg-gray-900/50 backdrop-blur sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-3 flex items-center justify-between">
          <h1 className="text-xl font-bold text-white">🤖 Job Hunt AI</h1>
          <ProfileNav />
        </div>
      </header>
      <main className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-8 space-y-6">{children}</main>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="bg-gray-800/60 border border-gray-700 rounded-lg px-3 py-1.5">
      <div className="text-xs text-gray-400">{label}</div>
      <div className="text-base font-bold text-white">{value}</div>
    </div>
  )
}

function EvaluationBlocks({ blocks }: { blocks: any }) {
  const sections: { key: string; label: string; render: (v: any) => React.ReactNode }[] = [
    {
      key: 'block_a_role_summary',
      label: 'A · Role Summary',
      render: (v) => (
        <div className="space-y-1.5 text-xs text-gray-300">
          {v.tldr && <p className="italic">"{v.tldr}"</p>}
          {v.level_signal && <p><span className="text-gray-500">Level:</span> {v.level_signal}</p>}
          {v.key_themes?.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {v.key_themes.map((t: string, i: number) => (
                <span key={i} className="bg-gray-800 border border-gray-700 px-1.5 py-0.5 rounded text-[10px]">{t}</span>
              ))}
            </div>
          )}
        </div>
      ),
    },
    {
      key: 'block_b_match',
      label: 'B · Match & Gaps',
      render: (v) => (
        <div className="space-y-2">
          {v.matched_requirements?.length > 0 && (
            <div>
              <div className="text-[10px] uppercase text-gray-500 mb-1">Matched</div>
              <ul className="text-xs text-gray-300 space-y-1">
                {v.matched_requirements.slice(0, 5).map((m: any, i: number) => (
                  <li key={i}>• <span className="text-emerald-400">{m.jd_requirement}</span> — {m.candidate_evidence}</li>
                ))}
              </ul>
            </div>
          )}
          {v.gaps?.length > 0 && (
            <div>
              <div className="text-[10px] uppercase text-gray-500 mb-1">Gaps</div>
              <ul className="text-xs text-gray-300 space-y-1">
                {v.gaps.slice(0, 5).map((g: any, i: number) => (
                  <li key={i}>
                    <span className={
                      g.severity === 'blocker' ? 'text-red-400' :
                      g.severity === 'gap' ? 'text-amber-400' : 'text-gray-400'
                    }>● </span>
                    {g.requirement} — <span className="text-gray-500">{g.mitigation}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      ),
    },
    {
      key: 'block_c_level_strategy',
      label: 'C · Level Strategy',
      render: (v) => (
        <div className="space-y-1.5 text-xs text-gray-300">
          {v.target_level && <p><span className="text-gray-500">Target:</span> {v.target_level}</p>}
          {v.selling_phrases?.length > 0 && (
            <ul className="space-y-1">
              {v.selling_phrases.map((p: string, i: number) => (
                <li key={i}>• "{p}"</li>
              ))}
            </ul>
          )}
        </div>
      ),
    },
    {
      key: 'block_e_personalization',
      label: 'E · Tailoring',
      render: (v) => (
        <div className="space-y-2">
          {v.summary_rewrite && (
            <div className="bg-gray-950 border border-gray-800 rounded p-2">
              <div className="text-[10px] uppercase text-gray-500 mb-1">Proposed summary</div>
              <p className="text-xs text-gray-300 italic">{v.summary_rewrite}</p>
            </div>
          )}
          {v.ats_keywords_to_inject?.length > 0 && (
            <div className="flex flex-wrap gap-1">
              {v.ats_keywords_to_inject.map((k: string, i: number) => (
                <span key={i} className="bg-violet-900/30 border border-violet-800 text-violet-300 px-1.5 py-0.5 rounded text-[10px]">{k}</span>
              ))}
            </div>
          )}
        </div>
      ),
    },
    {
      key: 'block_f_interview_prep',
      label: 'F · Interview Prep',
      render: (v) => (
        <div className="space-y-2">
          {v.likely_questions_with_star?.length > 0 && (
            <div className="space-y-2">
              {v.likely_questions_with_star.slice(0, 3).map((q: any, i: number) => (
                <div key={i} className="bg-gray-950 border border-gray-800 rounded p-2 text-xs text-gray-300">
                  <p className="font-semibold text-white">Q: {q.question}</p>
                  {q.star_situation && <p className="mt-1"><span className="text-gray-500">S:</span> {q.star_situation}</p>}
                  {q.action && <p><span className="text-gray-500">A:</span> {q.action}</p>}
                  {q.result && <p><span className="text-gray-500">R:</span> {q.result}</p>}
                </div>
              ))}
            </div>
          )}
        </div>
      ),
    },
  ]

  return (
    <div className="space-y-4">
      {sections.map((s) => {
        const v = blocks[s.key]
        if (!v) return null
        return (
          <div key={s.key}>
            <h3 className="text-xs font-semibold text-emerald-400 uppercase tracking-wider mb-2">
              {s.label}
            </h3>
            {s.render(v)}
          </div>
        )
      })}
      {blocks.tailoring_priorities?.length > 0 && (
        <div>
          <h3 className="text-xs font-semibold text-emerald-400 uppercase tracking-wider mb-2">
            Top Resume Changes (priority order)
          </h3>
          <ol className="text-xs text-gray-300 space-y-1 list-decimal list-inside">
            {blocks.tailoring_priorities.map((p: string, i: number) => (
              <li key={i}>{p}</li>
            ))}
          </ol>
        </div>
      )}
    </div>
  )
}

function ArtifactCard({
  title,
  path,
  artifact,
  preview,
}: {
  title: string
  path: string | null
  artifact?: { exists?: boolean; size?: number; content?: string; url?: string; kind?: string }
  preview?: boolean
}) {
  const exists = artifact?.exists
  const kind = artifact?.kind
  return (
    <section className="bg-gray-900 border border-gray-800 rounded-xl p-4">
      <h3 className="text-sm font-semibold text-white mb-2">{title}</h3>
      {!path ? (
        <p className="text-xs text-gray-500 italic">Not generated yet — click "Generate Resume" above.</p>
      ) : !exists ? (
        <p className="text-xs text-amber-400">
          ⚠ Generated, but artifact lost on redeploy. Click "Re-generate Resume" to rebuild.
        </p>
      ) : kind === 'remote' && artifact?.url ? (
        <>
          <a
            href={artifact.url}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 text-xs bg-blue-600 hover:bg-blue-500 text-white px-3 py-1.5 rounded-lg font-medium"
          >
            ⬇ Download
          </a>
          <p className="text-[10px] text-gray-500 mt-2 break-all">{path.split('/').pop()}</p>
        </>
      ) : (
        <>
          <p className="text-[10px] text-gray-500 break-all">{path.split('/').pop()}</p>
          {artifact?.size !== undefined && (
            <p className="text-[10px] text-gray-500">{(artifact.size / 1024).toFixed(1)} KB</p>
          )}
          {preview && artifact?.content && (
            <pre className="mt-2 text-[11px] text-gray-300 leading-relaxed whitespace-pre-wrap font-sans bg-gray-950 border border-gray-800 rounded p-2 max-h-64 overflow-y-auto">
              {artifact.content.slice(0, 2000)}
              {artifact.content.length > 2000 ? '\n\n... (truncated)' : ''}
            </pre>
          )}
        </>
      )}
    </section>
  )
}
