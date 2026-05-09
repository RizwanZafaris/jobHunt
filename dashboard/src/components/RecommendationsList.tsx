'use client'

import { useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
 dismissRecommendation,
 regenerateRecommendations,
 type Recommendation,
} from '@/lib/profile-api'

interface Props {
 initial: Recommendation[]
}

const KIND_LABEL: Record<string, string> = {
 missing_keyword: 'Missing keyword',
 weak_category: 'Weak category',
 conflict: 'Conflict',
 quantify: 'Unquantified',
}

const KIND_ICON: Record<string, string> = {
 missing_keyword: '',
 weak_category: '',
 conflict: '️',
 quantify: '',
}

const SEVERITY_COLOR: Record<string, string> = {
 high: 'bg-danger-bg/40 border-danger-border text-danger',
 medium: 'bg-warning-bg/40 border-warning-border text-warning',
 low: 'bg-info-bg/40 border-info-border text-info',
}

export default function RecommendationsList({ initial }: Props) {
 const router = useRouter()
 const [recs, setRecs] = useState(initial)
 const [filterKind, setFilterKind] = useState<string>('all')
 const [filterSeverity, setFilterSeverity] = useState<string>('all')
 const [refreshing, setRefreshing] = useState(false)
 const [refreshMsg, setRefreshMsg] = useState('')

 const filtered = useMemo(
 () =>
 recs.filter(
 (r) =>
 (filterKind === 'all' || r.kind === filterKind) &&
 (filterSeverity === 'all' || r.severity === filterSeverity)
 ),
 [recs, filterKind, filterSeverity]
 )

 const kinds = useMemo(() => Array.from(new Set(recs.map((r) => r.kind))), [recs])

 async function dismiss(id: number) {
 setRecs((prev) => prev.filter((r) => r.id !== id))
 try {
 await dismissRecommendation(id, true)
 } catch (e) {
 // restore on error
 setRecs(initial)
 }
 }

 async function regenerate() {
 setRefreshing(true)
 setRefreshMsg('Generating...')
 try {
 await regenerateRecommendations()
 setRefreshMsg(' Triggered — refresh in ~10s')
 setTimeout(() => {
 router.refresh()
 setRefreshMsg('')
 }, 10000)
 } catch (e: any) {
 setRefreshMsg(' ' + (e?.message || 'Failed'))
 } finally {
 setRefreshing(false)
 }
 }

 if (recs.length === 0) {
 return (
 <div className="bg-surface border border-border rounded-xl p-8 text-center">
 <h2 className="text-lg font-semibold text-fg mb-2">No recommendations yet</h2>
 <p className="text-sm text-fg-muted mb-4">
 Click the button below to run the analyzer.
 </p>
 <button
 onClick={regenerate}
 disabled={refreshing}
 className="text-xs bg-info-bg hover:bg-info-bg disabled:opacity-50 text-fg px-4 py-2 rounded-lg font-medium"
 >
 {refreshing ? 'Generating...' : 'Generate recommendations'}
 </button>
 {refreshMsg && <p className="text-xs text-fg-muted mt-3">{refreshMsg}</p>}
 </div>
 )
 }

 return (
 <>
 <div className="flex flex-col sm:flex-row gap-2 items-start sm:items-center justify-between">
 <div className="flex flex-wrap gap-2">
 <select
 value={filterKind}
 onChange={(e) => setFilterKind(e.target.value)}
 className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg"
 >
 <option value="all">All kinds</option>
 {kinds.map((k) => (
 <option key={k} value={k}>
 {KIND_LABEL[k] || k}
 </option>
 ))}
 </select>
 <select
 value={filterSeverity}
 onChange={(e) => setFilterSeverity(e.target.value)}
 className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg"
 >
 <option value="all">All severities</option>
 <option value="high">High</option>
 <option value="medium">Medium</option>
 <option value="low">Low</option>
 </select>
 <span className="text-xs text-fg-subtle self-center">
 {filtered.length} of {recs.length} shown
 </span>
 </div>
 <div className="flex items-center gap-2">
 {refreshMsg && <span className="text-xs text-fg-muted">{refreshMsg}</span>}
 <button
 onClick={regenerate}
 disabled={refreshing}
 className="text-xs bg-surface-raised hover:bg-border-strong disabled:opacity-50 text-fg border border-border-strong px-3 py-1.5 rounded-lg font-medium"
 >
 {refreshing ? ' Generating...' : ' Regenerate'}
 </button>
 </div>
 </div>

 <div className="space-y-3 mt-3">
 {filtered.map((r) => {
 const sevColor = SEVERITY_COLOR[r.severity] || SEVERITY_COLOR.medium
 return (
 <article
 key={r.id}
 className="bg-surface border border-border rounded-xl p-4 hover:border-border-strong transition-colors"
 >
 <div className="flex items-start gap-3">
 <div className="text-xl shrink-0">{KIND_ICON[r.kind] || ''}</div>
 <div className="flex-1 min-w-0">
 <div className="flex items-center gap-2 flex-wrap">
 <h3 className="text-sm font-semibold text-fg">{r.title}</h3>
 <span className={`text-2xs uppercase tracking-wider border px-2 py-0.5 rounded ${sevColor}`}>
 {r.severity}
 </span>
 <span className="text-2xs uppercase tracking-wider text-fg-subtle">
 {KIND_LABEL[r.kind] || r.kind}
 </span>
 </div>
 {r.detail && (
 <p className="text-xs text-fg-muted mt-1.5 leading-relaxed whitespace-pre-wrap">
 {r.detail}
 </p>
 )}
 {(r.related_keyword || r.related_category) && (
 <div className="mt-2 flex flex-wrap gap-1.5">
 {r.related_keyword && (
 <span className="text-2xs bg-surface-raised border border-border-strong text-fg-muted px-2 py-0.5 rounded">
 keyword: {r.related_keyword}
 </span>
 )}
 {r.related_category && (
 <span className="text-2xs bg-surface-raised border border-border-strong text-fg-muted px-2 py-0.5 rounded">
 category: {r.related_category}
 </span>
 )}
 </div>
 )}
 </div>
 <button
 onClick={() => dismiss(r.id)}
 className="text-xs text-fg-subtle hover:text-fg-muted px-2 py-1 rounded hover:bg-surface-raised shrink-0"
 title="Dismiss"
 >
 Dismiss
 </button>
 </div>
 </article>
 )
 })}
 </div>
 </>
 )
}
