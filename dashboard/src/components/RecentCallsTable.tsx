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
 // BUG-017: relative timestamps were rendered with Date.now() during SSR,
 // which produced a different value on the client and triggered hydration
 // warnings. We render the deterministic absolute UTC time on the server
 // (and again on the client's first paint), then upgrade to relative
 // ("13 m ago") after mount where Date.now() is safe.
 const [mounted, setMounted] = useState(false)
 useEffect(() => {
   setMounted(true)
 }, [])

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
 <section className="bg-surface border border-border rounded-xl overflow-hidden">
 <div className="px-4 py-3 border-b border-border flex flex-wrap items-center gap-2 justify-between">
 <div>
 <h2 className="text-base font-semibold text-fg">Recent Calls</h2>
 <p className="text-2xs text-fg-subtle mt-0.5">
 Individual LLM calls · most recent first
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
 className="text-xs bg-surface-raised border border-border-strong rounded-lg px-2 py-1.5 text-fg"
 >
 <option value="all">All providers</option>
 {providers.map((p) => (
 <option key={p} value={p}>
 {p}
 </option>
 ))}
 </select>
 <label className="text-xs text-fg-muted flex items-center gap-1.5 cursor-pointer">
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
 className="text-xs bg-surface-raised border border-border-strong rounded-lg px-2 py-1.5 text-fg"
 >
 <option value={50}>50</option>
 <option value={100}>100</option>
 <option value={250}>250</option>
 <option value={500}>500</option>
 </select>
 {loading && <span className="text-2xs text-fg-subtle">loading…</span>}
 </div>
 </div>

 {warning && (
 <div className="px-4 py-3 text-2xs text-warning bg-warning-bg/10 border-b border-warning-border/30">
 {warning}
 </div>
 )}

 <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
 <table className="w-full text-xs">
 <thead className="bg-bg/80 text-fg-muted uppercase text-2xs sticky top-0">
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
 <tbody className="divide-y divide-border/60">
 {calls.length === 0 ? (
 <tr>
 <td colSpan={8} className="px-3 py-10 text-center text-sm text-fg-subtle italic">
 No calls match.
 </td>
 </tr>
 ) : (
 calls.map((c) => (
 <tr
 key={c.id}
 className={`hover:bg-surface-raised/30 ${c.error ? 'bg-danger-bg/30' : ''}`}
 >
 <td className="px-3 py-1.5 text-fg-muted whitespace-nowrap" title={c.called_at}>
 {mounted ? relativeTime(c.called_at) : absoluteUtc(c.called_at)}
 </td>
 <td className="px-3 py-1.5">
 <code className="text-info font-mono text-2xs">
 {c.agent_name || '(none)'}
 </code>
 </td>
 <td className="px-3 py-1.5">
 <span className="text-2xs bg-surface-raised border border-border-strong px-1.5 rounded text-fg-muted">
 {c.provider}
 </span>
 <div className="text-2xs text-fg-subtle mt-0.5 truncate max-w-[12rem]">
 {c.model}
 </div>
 </td>
 <td className="px-3 py-1.5 text-right text-fg-muted whitespace-nowrap">
 {c.input_tokens}
 <span className="text-fg-subtle"> / </span>
 {c.output_tokens}
 </td>
 <td className="px-3 py-1.5 text-right text-fg-muted whitespace-nowrap">
 ${Number(c.cost_usd).toFixed(4)}
 </td>
 <td className="px-3 py-1.5 text-right text-fg-muted whitespace-nowrap">
 {c.latency_ms}ms
 </td>
 <td className="px-3 py-1.5 text-fg-subtle text-2xs truncate max-w-[10rem]">
 {c.resume_build_id ? c.resume_build_id.slice(0, 8) : '—'}
 </td>
 <td className="px-3 py-1.5 text-danger text-2xs truncate max-w-[14rem]">
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
 return d.toLocaleDateString('en-US', { day: 'numeric', month: 'short', timeZone: 'UTC' })
}

// Deterministic SSR-safe rendering — locale + tz pinned so the server and
// the client produce the exact same string. Used until the client mounts
// and we can upgrade to a relative time.
function absoluteUtc(iso: string): string {
 if (!iso) return ''
 return new Date(iso).toLocaleString('en-US', {
   day: 'numeric',
   month: 'short',
   hour: '2-digit',
   minute: '2-digit',
   timeZone: 'UTC',
   hour12: false,
 })
}
