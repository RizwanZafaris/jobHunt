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
  badgeClassName = 'bg-surface-raised border border-border-strong text-fg-muted',
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
          className="opacity-0 group-hover:opacity-100 text-2xs bg-info-bg hover:bg-info-bg text-fg px-2 py-1 rounded transition-opacity"
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
              className="opacity-70 hover:opacity-100 hover:text-danger"
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
          className="bg-surface-raised border border-border-strong rounded px-2 py-1 text-xs text-fg outline-none focus:ring-1 focus:ring-blue-500 w-64"
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
          className="text-xs bg-info-bg hover:bg-info-bg disabled:opacity-50 text-fg px-2 py-1 rounded"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
        <button onClick={cancel} className="text-xs bg-border-strong text-fg px-2 py-1 rounded">
          Cancel
        </button>
        {error && <span className="text-xs text-danger">{error}</span>}
      </div>
    </div>
  )
}
