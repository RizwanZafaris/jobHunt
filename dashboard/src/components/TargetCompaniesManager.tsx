'use client'

import Link from 'next/link'
import { useMemo, useState } from 'react'
import { useRouter } from 'next/navigation'
import {
 addTargetCompany,
 reclassifyJobs,
 removeTargetCompany,
 runTargetsPipeline,
 triggerCompanyResearch,
 updateCompany,
 type TargetCompaniesResponse,
 type TargetCompany,
} from '@/lib/profile-api'

interface Props {
 initial: TargetCompaniesResponse
}

const PRIORITY_COLOR: Record<string, string> = {
 high: 'bg-success-bg/40 border-success-border text-success',
 medium: 'bg-info-bg/40 border-info-border text-info',
 low: 'bg-surface-raised border-border-strong text-fg-muted',
}

export default function TargetCompaniesManager({ initial }: Props) {
 const router = useRouter()
 const [companies, setCompanies] = useState<TargetCompany[]>(initial.companies)
 const [filter, setFilter] = useState('')
 const [filterCat, setFilterCat] = useState('all')
 const [running, setRunning] = useState(false)
 const [runMsg, setRunMsg] = useState('')

 const [addOpen, setAddOpen] = useState(false)
 const [newName, setNewName] = useState('')
 const [newCategory, setNewCategory] = useState('')
 const [newPriority, setNewPriority] = useState('medium')
 const [newCareersUrl, setNewCareersUrl] = useState('')

 const cats = useMemo(
 () => Array.from(new Set(companies.map((c) => c.category || 'Uncategorized'))).sort(),
 [companies]
 )

 const filtered = useMemo(
 () =>
 companies.filter(
 (c) =>
 (filterCat === 'all' || (c.category || 'Uncategorized') === filterCat) &&
 (filter === '' || c.name.toLowerCase().includes(filter.toLowerCase()))
 ),
 [companies, filter, filterCat]
 )

 async function changePriority(c: TargetCompany, next: string) {
 const updated = { ...c, priority: next as TargetCompany['priority'] }
 setCompanies((arr) => arr.map((x) => (x.id === c.id ? updated : x)))
 try {
 await updateCompany(c.id, { priority: next as any })
 } catch (e) {
 setCompanies((arr) => arr.map((x) => (x.id === c.id ? c : x)))
 }
 }

 async function remove(c: TargetCompany) {
 setCompanies((arr) => arr.filter((x) => x.id !== c.id))
 try {
 await removeTargetCompany(c.id)
 } catch (e) {
 setCompanies(initial.companies)
 }
 }

 async function add() {
 if (!newName.trim()) return
 try {
 const result = await addTargetCompany({
 name: newName.trim(),
 category: newCategory.trim() || undefined,
 priority: newPriority,
 careers_url: newCareersUrl.trim() || undefined,
 })
 if (result?.row) {
 setCompanies((arr) => [...arr, result.row])
 }
 setNewName('')
 setNewCategory('')
 setNewCareersUrl('')
 setAddOpen(false)
 router.refresh()
 } catch (e: any) {
 alert(`Failed: ${e.message}`)
 }
 }

 async function runPipeline() {
 setRunning(true)
 setRunMsg('Triggering pipeline on all targets...')
 try {
 await runTargetsPipeline()
 setRunMsg(' Pipeline started in background')
 } catch (e: any) {
 setRunMsg(` ${e.message}`)
 } finally {
 setRunning(false)
 setTimeout(() => setRunMsg(''), 6000)
 }
 }

 async function researchAll(priority?: string) {
 setRunning(true)
 setRunMsg(`Researching ${priority || 'all'} targets — runs ~30-90s per company...`)
 try {
 await triggerCompanyResearch({ priority })
 setRunMsg(' Research started in background — refresh in a few minutes')
 } catch (e: any) {
 setRunMsg(` ${e.message}`)
 } finally {
 setRunning(false)
 setTimeout(() => setRunMsg(''), 8000)
 }
 }

 async function reclassify() {
 setRunning(true)
 setRunMsg('Re-classifying jobs missing archetype + legitimacy...')
 try {
 await reclassifyJobs(true)
 setRunMsg(' Reclassification started — refresh job list in ~5 min')
 } catch (e: any) {
 setRunMsg(` ${e.message}`)
 } finally {
 setRunning(false)
 setTimeout(() => setRunMsg(''), 8000)
 }
 }

 const byCat: Record<string, TargetCompany[]> = {}
 for (const c of filtered) {
 const k = c.category || 'Uncategorized'
 byCat[k] = byCat[k] || []
 byCat[k].push(c)
 }

 return (
 <>
 <div className="flex flex-col sm:flex-row gap-2 items-start sm:items-center justify-between">
 <div className="flex flex-wrap gap-2">
 <input
 value={filter}
 onChange={(e) => setFilter(e.target.value)}
 placeholder="Search company..."
 aria-label="Filter target companies by name"
 className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg w-48"
 />
 <select
 value={filterCat}
 onChange={(e) => setFilterCat(e.target.value)}
 aria-label="Filter target companies by category"
 className="text-xs bg-surface-raised border border-border-strong rounded-lg px-3 py-1.5 text-fg"
 >
 <option value="all">All categories</option>
 {cats.map((c) => (
 <option key={c} value={c}>
 {c}
 </option>
 ))}
 </select>
 <span className="text-xs text-fg-subtle self-center">
 {filtered.length} of {companies.length} shown
 </span>
 </div>
 <div className="flex items-center gap-2 flex-wrap">
 {runMsg && <span className="text-xs text-fg-muted">{runMsg}</span>}
 <button
 onClick={() => setAddOpen(true)}
 className="text-xs bg-surface-raised hover:bg-border-strong border border-border-strong text-fg px-3 py-1.5 rounded-lg font-medium"
 >
 + Add
 </button>
 <button
 onClick={() => researchAll('high')}
 disabled={running}
 className="text-xs bg-surface-raised hover:bg-border-strong disabled:opacity-50 border border-border-strong text-fg px-3 py-1.5 rounded-lg font-medium"
 title="Research only high-priority targets (~30 companies, ~30 min)"
 >
 Research High-Priority
 </button>
 <button
 onClick={() => researchAll()}
 disabled={running}
 className="text-xs bg-surface-raised hover:bg-border-strong disabled:opacity-50 border border-border-strong text-fg px-3 py-1.5 rounded-lg font-medium"
 title="Research ALL 68 targets (~60 min, ~$10 in tokens)"
 >
 Research All
 </button>
 <button
 onClick={reclassify}
 disabled={running}
 className="text-xs bg-surface-raised hover:bg-border-strong disabled:opacity-50 border border-border-strong text-fg px-3 py-1.5 rounded-lg font-medium"
 title="Re-classify older jobs missing archetype + legitimacy"
 >
 ️ Reclassify Old Jobs
 </button>
 <button
 onClick={runPipeline}
 disabled={running}
 className="text-xs bg-info-bg hover:bg-info-bg disabled:opacity-50 text-fg px-3 py-1.5 rounded-lg font-medium"
 >
 {running ? ' Starting...' : ' Run Pipeline on All Targets'}
 </button>
 </div>
 </div>

 {addOpen && (
 <div className="bg-surface border border-info-border rounded-xl p-4 space-y-2">
 <h3 className="text-sm font-semibold text-fg">Add target company</h3>
 <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
 <input
 value={newName}
 onChange={(e) => setNewName(e.target.value)}
 placeholder="Company name"
 aria-label="New target company name"
 className="text-xs bg-surface-raised border border-border-strong rounded px-2 py-1.5 text-fg"
 />
 <input
 value={newCategory}
 onChange={(e) => setNewCategory(e.target.value)}
 placeholder="Category"
 aria-label="New target company category"
 className="text-xs bg-surface-raised border border-border-strong rounded px-2 py-1.5 text-fg"
 />
 <select
 value={newPriority}
 onChange={(e) => setNewPriority(e.target.value)}
 aria-label="New target company priority"
 className="text-xs bg-surface-raised border border-border-strong rounded px-2 py-1.5 text-fg"
 >
 <option value="high">High</option>
 <option value="medium">Medium</option>
 <option value="low">Low</option>
 </select>
 <input
 value={newCareersUrl}
 onChange={(e) => setNewCareersUrl(e.target.value)}
 placeholder="Careers URL"
 aria-label="New target company careers URL"
 className="text-xs bg-surface-raised border border-border-strong rounded px-2 py-1.5 text-fg"
 />
 </div>
 <div className="flex gap-2">
 <button
 onClick={add}
 className="text-xs bg-info-bg hover:bg-info-bg text-fg px-3 py-1 rounded"
 >
 Add
 </button>
 <button
 onClick={() => setAddOpen(false)}
 className="text-xs bg-border-strong text-fg px-3 py-1 rounded"
 >
 Cancel
 </button>
 </div>
 </div>
 )}

 <div className="space-y-5">
 {Object.entries(byCat)
 .sort((a, b) => b[1].length - a[1].length)
 .map(([cat, items]) => (
 <div key={cat} className="bg-surface border border-border rounded-xl p-4">
 <h3 className="text-sm font-semibold text-fg mb-3 flex items-center gap-2">
 {cat}
 <span className="text-xs text-fg-subtle font-normal">({items.length})</span>
 </h3>
 <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
 {items.map((c) => (
 <div
 key={c.id}
 className="bg-surface-raised/50 border border-border rounded-lg p-3 hover:border-border-strong transition-colors group"
 >
 <div className="flex items-center justify-between mb-1">
 <Link
 href={`/companies/${encodeURIComponent(c.name)}`}
 className="text-sm font-medium text-fg truncate hover:text-info flex-1"
 title="Open research intel"
 >
 {c.name}
 </Link>
 <button
 onClick={() => remove(c)}
 className="opacity-0 group-hover:opacity-100 text-xs text-danger hover:text-danger"
 title="Remove from targets"
 >
 ×
 </button>
 </div>
 <div className="flex items-center gap-2 text-xs">
 <select
 value={c.priority}
 onChange={(e) => changePriority(c, e.target.value)}
 aria-label={`Priority for ${c.name}`}
 className={`text-2xs border px-1.5 py-0 rounded ${PRIORITY_COLOR[c.priority] || PRIORITY_COLOR.medium}`}
 >
 <option value="high">high</option>
 <option value="medium">medium</option>
 <option value="low">low</option>
 </select>
 <Link
 href={`/companies/${encodeURIComponent(c.name)}`}
 className="text-2xs text-info hover:text-info"
 title="View research"
 >
 intel 
 </Link>
 {c.careers_url && (
 <a
 href={c.careers_url}
 target="_blank"
 rel="noopener noreferrer"
 className="text-2xs text-info hover:text-info truncate flex-1"
 onClick={(e) => e.stopPropagation()}
 >
 careers 
 </a>
 )}
 </div>
 {c.last_scanned_at && (
 <div className="text-2xs text-fg-subtle mt-1">
 scanned {new Date(c.last_scanned_at).toLocaleDateString('en-US', { day: 'numeric', month: 'short', timeZone: 'UTC' })}
 </div>
 )}
 </div>
 ))}
 </div>
 </div>
 ))}
 </div>
 </>
 )
}
