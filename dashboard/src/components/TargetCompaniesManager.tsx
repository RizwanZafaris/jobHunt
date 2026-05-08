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
  high: 'bg-emerald-900/40 border-emerald-800 text-emerald-300',
  medium: 'bg-blue-900/40 border-blue-800 text-blue-300',
  low: 'bg-gray-800 border-gray-700 text-gray-400',
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
      setRunMsg('✅ Pipeline started in background')
    } catch (e: any) {
      setRunMsg(`❌ ${e.message}`)
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
      setRunMsg('✅ Research started in background — refresh in a few minutes')
    } catch (e: any) {
      setRunMsg(`❌ ${e.message}`)
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
      setRunMsg('✅ Reclassification started — refresh job list in ~5 min')
    } catch (e: any) {
      setRunMsg(`❌ ${e.message}`)
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
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200 w-48"
          />
          <select
            value={filterCat}
            onChange={(e) => setFilterCat(e.target.value)}
            className="text-xs bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-gray-200"
          >
            <option value="all">All categories</option>
            {cats.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <span className="text-xs text-gray-500 self-center">
            {filtered.length} of {companies.length} shown
          </span>
        </div>
        <div className="flex items-center gap-2 flex-wrap">
          {runMsg && <span className="text-xs text-gray-400">{runMsg}</span>}
          <button
            onClick={() => setAddOpen(true)}
            className="text-xs bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-200 px-3 py-1.5 rounded-lg font-medium"
          >
            + Add
          </button>
          <button
            onClick={() => researchAll('high')}
            disabled={running}
            className="text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-50 border border-gray-700 text-gray-200 px-3 py-1.5 rounded-lg font-medium"
            title="Research only high-priority targets (~30 companies, ~30 min)"
          >
            🔬 Research High-Priority
          </button>
          <button
            onClick={() => researchAll()}
            disabled={running}
            className="text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-50 border border-gray-700 text-gray-200 px-3 py-1.5 rounded-lg font-medium"
            title="Research ALL 68 targets (~60 min, ~$10 in tokens)"
          >
            🔬 Research All
          </button>
          <button
            onClick={reclassify}
            disabled={running}
            className="text-xs bg-gray-800 hover:bg-gray-700 disabled:opacity-50 border border-gray-700 text-gray-200 px-3 py-1.5 rounded-lg font-medium"
            title="Re-classify older jobs missing archetype + legitimacy"
          >
            🏷️ Reclassify Old Jobs
          </button>
          <button
            onClick={runPipeline}
            disabled={running}
            className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-3 py-1.5 rounded-lg font-medium"
          >
            {running ? '⏳ Starting...' : '🎯 Run Pipeline on All Targets'}
          </button>
        </div>
      </div>

      {addOpen && (
        <div className="bg-gray-900 border border-blue-700 rounded-xl p-4 space-y-2">
          <h3 className="text-sm font-semibold text-white">Add target company</h3>
          <div className="grid grid-cols-1 sm:grid-cols-4 gap-2">
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Company name"
              className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white"
            />
            <input
              value={newCategory}
              onChange={(e) => setNewCategory(e.target.value)}
              placeholder="Category"
              className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white"
            />
            <select
              value={newPriority}
              onChange={(e) => setNewPriority(e.target.value)}
              className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white"
            >
              <option value="high">High</option>
              <option value="medium">Medium</option>
              <option value="low">Low</option>
            </select>
            <input
              value={newCareersUrl}
              onChange={(e) => setNewCareersUrl(e.target.value)}
              placeholder="Careers URL"
              className="text-xs bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-white"
            />
          </div>
          <div className="flex gap-2">
            <button
              onClick={add}
              className="text-xs bg-blue-600 hover:bg-blue-500 text-white px-3 py-1 rounded"
            >
              Add
            </button>
            <button
              onClick={() => setAddOpen(false)}
              className="text-xs bg-gray-700 text-gray-200 px-3 py-1 rounded"
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
            <div key={cat} className="bg-gray-900 border border-gray-800 rounded-xl p-4">
              <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
                {cat}
                <span className="text-xs text-gray-500 font-normal">({items.length})</span>
              </h3>
              <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-2">
                {items.map((c) => (
                  <div
                    key={c.id}
                    className="bg-gray-800/50 border border-gray-800 rounded-lg p-3 hover:border-gray-700 transition-colors group"
                  >
                    <div className="flex items-center justify-between mb-1">
                      <Link
                        href={`/companies/${encodeURIComponent(c.name)}`}
                        className="text-sm font-medium text-white truncate hover:text-blue-400 flex-1"
                        title="Open research intel"
                      >
                        {c.name}
                      </Link>
                      <button
                        onClick={() => remove(c)}
                        className="opacity-0 group-hover:opacity-100 text-xs text-red-400 hover:text-red-300"
                        title="Remove from targets"
                      >
                        ×
                      </button>
                    </div>
                    <div className="flex items-center gap-2 text-xs">
                      <select
                        value={c.priority}
                        onChange={(e) => changePriority(c, e.target.value)}
                        className={`text-[10px] border px-1.5 py-0 rounded ${PRIORITY_COLOR[c.priority] || PRIORITY_COLOR.medium}`}
                      >
                        <option value="high">high</option>
                        <option value="medium">medium</option>
                        <option value="low">low</option>
                      </select>
                      <Link
                        href={`/companies/${encodeURIComponent(c.name)}`}
                        className="text-[10px] text-blue-400 hover:text-blue-300"
                        title="View research"
                      >
                        intel →
                      </Link>
                      {c.careers_url && (
                        <a
                          href={c.careers_url}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="text-[10px] text-blue-400 hover:text-blue-300 truncate flex-1"
                          onClick={(e) => e.stopPropagation()}
                        >
                          careers ↗
                        </a>
                      )}
                    </div>
                    {c.last_scanned_at && (
                      <div className="text-[10px] text-gray-500 mt-1">
                        scanned {new Date(c.last_scanned_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })}
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
