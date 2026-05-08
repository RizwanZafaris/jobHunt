'use client'

import { useState } from 'react'

interface Props {
  values: string[]
  onChange: (next: string[]) => Promise<void>
  badgeClassName?: string
  addPlaceholder?: string
}

export default function EditableTagList({
  values,
  onChange,
  badgeClassName = 'bg-gray-800 border border-gray-700 text-gray-300',
  addPlaceholder = 'Add new...',
}: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<string[]>(values)
  const [adding, setAdding] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  function startEdit() {
    setDraft(values)
    setEditing(true)
  }

  function cancel() {
    setDraft(values)
    setAdding('')
    setEditing(false)
    setError(null)
  }

  async function commit() {
    const next = [...draft]
    const trimmed = adding.trim()
    if (trimmed) next.push(trimmed)
    setSaving(true)
    setError(null)
    try {
      await onChange(next)
      setEditing(false)
      setAdding('')
    } catch (e: any) {
      setError(e?.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  if (!editing) {
    return (
      <div className="flex flex-wrap gap-2 items-center group">
        {values.map((v) => (
          <span key={v} className={`text-xs px-2.5 py-1 rounded-full ${badgeClassName}`}>
            {v}
          </span>
        ))}
        <button
          onClick={startEdit}
          className="opacity-0 group-hover:opacity-100 text-[10px] bg-blue-600 hover:bg-blue-500 text-white px-2 py-1 rounded transition-opacity"
        >
          edit list
        </button>
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {draft.map((v, i) => (
          <span key={`${v}-${i}`} className={`text-xs px-2 py-0.5 rounded-full inline-flex items-center gap-1 ${badgeClassName}`}>
            {v}
            <button
              onClick={() => setDraft((d) => d.filter((_, idx) => idx !== i))}
              className="opacity-70 hover:opacity-100 hover:text-red-400"
              aria-label={`Remove ${v}`}
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex items-center gap-2">
        <input
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          placeholder={addPlaceholder}
          className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-white outline-none focus:ring-1 focus:ring-blue-500 w-64"
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              if (adding.trim()) {
                setDraft((d) => [...d, adding.trim()])
                setAdding('')
              }
            }
            if (e.key === 'Escape') cancel()
          }}
        />
        <button
          onClick={commit}
          disabled={saving}
          className="text-xs bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-2 py-1 rounded"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
        <button onClick={cancel} className="text-xs bg-gray-700 text-gray-200 px-2 py-1 rounded">
          Cancel
        </button>
        {error && <span className="text-xs text-red-400">{error}</span>}
      </div>
    </div>
  )
}
