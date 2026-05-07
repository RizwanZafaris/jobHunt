'use client'

interface Props {
  digest: any
}

export default function DigestPanel({ digest }: Props) {
  if (!digest || digest.message) {
    return (
      <div className="bg-gray-900 rounded-xl border border-gray-800 p-6 h-full flex flex-col">
        <h2 className="text-base font-semibold text-white mb-4">Daily Digest</h2>
        <div className="flex-1 flex items-center justify-center">
          <p className="text-gray-500 text-sm text-center">
            No digest yet.<br />
            <span className="text-xs">Run the boss agent to generate one.</span>
          </p>
        </div>
      </div>
    )
  }

  const content = digest.digest_content || ''
  const runDate = digest.run_date || digest.created_at?.split('T')[0]

  return (
    <div className="bg-gray-900 rounded-xl border border-gray-800 p-6 h-full flex flex-col">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-base font-semibold text-white">Daily Digest</h2>
        {runDate && (
          <span className="text-xs text-gray-500">
            {new Date(runDate).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
          </span>
        )}
      </div>

      {/* Quick stats from digest */}
      <div className="grid grid-cols-2 gap-2 mb-4">
        <div className="bg-gray-800 rounded-lg p-2 text-center">
          <div className="text-lg font-bold text-blue-400">{digest.jobs_found_today ?? 0}</div>
          <div className="text-xs text-gray-400">Jobs today</div>
        </div>
        <div className="bg-gray-800 rounded-lg p-2 text-center">
          <div className="text-lg font-bold text-emerald-400">{digest.jobs_scored_high ?? 0}</div>
          <div className="text-xs text-gray-400">High match</div>
        </div>
        <div className="bg-gray-800 rounded-lg p-2 text-center">
          <div className="text-lg font-bold text-purple-400">{digest.applications_sent ?? 0}</div>
          <div className="text-xs text-gray-400">Applied</div>
        </div>
        <div className="bg-gray-800 rounded-lg p-2 text-center">
          <div className="text-lg font-bold text-amber-400">{digest.stale_companies ?? 0}</div>
          <div className="text-xs text-gray-400">Stale co.</div>
        </div>
      </div>

      {/* Digest text */}
      <div className="flex-1 overflow-y-auto">
        <pre className="text-xs text-gray-300 whitespace-pre-wrap font-sans leading-relaxed">
          {content.length > 600 ? content.slice(0, 600) + '...' : content}
        </pre>
      </div>

      {digest.digest_sent && (
        <div className="mt-3 flex items-center gap-1 text-xs text-emerald-500">
          <span>✅</span>
          <span>Email delivered</span>
        </div>
      )}
    </div>
  )
}
