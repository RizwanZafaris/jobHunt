'use client'

import { useEffect, useMemo, useState } from 'react'
import { fetchRecentCalls, type AgentCallLogRow } from '@/lib/profile-api'

interface Props {
  initial: AgentCallLogRow[]
  warning?: string
}

export default function RecentCallsTable({ initial, warning: initialWarning }: Props) {
  const [calls, setCalls] = useState<AgentCallLogRow[]>(initial)
  const [warning, setWarning] = useState<string | undefined>(initialWarning)
  const [providerFilter, setProviderFilter] = useState<string>('all')
  const [errorOnly, setErrorOnly] = useState(false)
  const [limit, setLimit] = useState(100)
  const [loading, setLoading] = useState(false)

  const providers = useMemo(() => {
    const s = new Set<string>()
    for (const c of calls) s.add(c.provider)
    return Array.from(s).sort()
  }, [calls])

  async function reload(opts: {
    limit?: number
    provider?: string
    errorOnly?: boolean
  }) {
    setLoading(true)
    try {
      const r = await fetchRecentCalls({
        limit: opts.limit ?? limit,
        provider:
          opts.provider !== undefined
            ? opts.provider === 'all'
              ? undefined
              : opts.provider
            : providerFilter === 'all'
            ? undefined
            : providerFilter,
        has_error: opts.errorOnly ?? errorOnly ? true : undefined,
      })
      setCalls(r.calls)
      setWarning(r.warning)
    } catch (e: any) {
      setWarning(e?.message || 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="bg-gray-900 border border-gray-800 rounded-xl overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-800 flex flex-wrap items-center gap-2 justify-between">
        <div>
          <h2 className="text-base font-semibold text-white">Recent Calls</h2>
          <p className="text-[11px] text-gray-500 mt-0.5">
            Raw <code className="text-gray-400">agent_call_log</code> rows · most recent first
          </p>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          <select
            value={providerFilter}
            onChange={(e) => {
              setProviderFilter(e.target.value)
              reload({ provider: e.target.value })
            }}
            disabled={loading}
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-gray-200"
          >
            <option value="all">All providers</option>
            {providers.map((p) => (
              <option key={p} value={p}>
                {p}
              </option>
            ))}
          </select>
          <label className="text-xs text-gray-300 flex items-center gap-1.5 cursor-pointer">
            <input
              type="checkbox"
              checked={errorOnly}
              onChange={(e) => {
                setErrorOnly(e.target.checked)
                reload({ errorOnly: e.target.checked })
              }}
              disabled={loading}
              className="accent-red-500"
            />
            Errors only
          </label>
          <select
            value={limit}
            onChange={(e) => {
              const n = Number(e.target.value)
              setLimit(n)
              reload({ limit: n })
            }}
            disabled={loading}
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-gray-200"
          >
            <option value={50}>50</option>
            <option value={100}>100</option>
            <option value={250}>250</option>
            <option value={500}>500</option>
          </select>
          {loading && <span className="text-[11px] text-gray-500">loading…</span>}
        </div>
      </div>

      {warning && (
        <div className="px-4 py-3 text-[11px] text-amber-400 bg-amber-900/10 border-b border-amber-900/30">
          ⚠ {warning}
        </div>
      )}

      <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="bg-gray-950/80 text-gray-400 uppercase text-[10px] sticky top-0">
            <tr>
              <th className="px-3 py-2 text-left font-semibold tracking-wider">When</th>
              <th className="px-3 py-2 text-left font-semibold tracking-wider">Agent</th>
              <th className="px-3 py-2 text-left font-semibold tracking-wider">Model</th>
              <th className="px-3 py-2 text-right font-semibold tracking-wider">Tokens</th>
              <th className="px-3 py-2 text-right font-semibold tracking-wider">Cost</th>
              <th className="px-3 py-2 text-right font-semibold tracking-wider">Latency</th>
              <th className="px-3 py-2 text-left font-semibold tracking-wider">Build</th>
              <th className="px-3 py-2 text-left font-semibold tracking-wider">Error</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-800/60">
            {calls.length === 0 ? (
              <tr>
                <td colSpan={8} className="px-3 py-10 text-center text-sm text-gray-500 italic">
                  No calls match.
                </td>
              </tr>
            ) : (
              calls.map((c) => (
                <tr
                  key={c.id}
                  className={`hover:bg-gray-800/30 ${c.error ? 'bg-red-950/30' : ''}`}
                >
                  <td className="px-3 py-1.5 text-gray-400 whitespace-nowrap" title={c.called_at}>
                    {relativeTime(c.called_at)}
                  </td>
                  <td className="px-3 py-1.5">
                    <code className="text-blue-400 font-mono text-[11px]">
                      {c.agent_name || '(none)'}
                    </code>
                  </td>
                  <td className="px-3 py-1.5">
                    <span className="text-[10px] bg-gray-800 border border-gray-700 px-1.5 rounded text-gray-300">
                      {c.provider}
                    </span>
                    <div className="text-[10px] text-gray-500 mt-0.5 truncate max-w-[12rem]">
                      {c.model}
                    </div>
                  </td>
                  <td className="px-3 py-1.5 text-right text-gray-400 whitespace-nowrap">
                    {c.input_tokens}
                    <span className="text-gray-700"> / </span>
                    {c.output_tokens}
                  </td>
                  <td className="px-3 py-1.5 text-right text-gray-300 whitespace-nowrap">
                    ${Number(c.cost_usd).toFixed(4)}
                  </td>
                  <td className="px-3 py-1.5 text-right text-gray-400 whitespace-nowrap">
                    {c.latency_ms}ms
                  </td>
                  <td className="px-3 py-1.5 text-gray-500 text-[10px] truncate max-w-[10rem]">
                    {c.resume_build_id ? c.resume_build_id.slice(0, 8) : '—'}
                  </td>
                  <td className="px-3 py-1.5 text-red-400 text-[10px] truncate max-w-[14rem]">
                    {c.error || ''}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function relativeTime(iso: string): string {
  if (!iso) return ''
  const d = new Date(iso)
  const now = Date.now()
  const diffSec = Math.floor((now - d.getTime()) / 1000)
  if (diffSec < 60) return `${diffSec}s ago`
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`
  if (diffSec < 86400) return `${Math.floor(diffSec / 3600)}h ago`
  if (diffSec < 86400 * 7) return `${Math.floor(diffSec / 86400)}d ago`
  return d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}
