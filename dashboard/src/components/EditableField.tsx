'use client'

import { useEffect, useRef, useState } from 'react'

interface Props {
  value: string
  onSave: (next: string) => Promise<void>
  multiline?: boolean
  placeholder?: string
  className?: string
  inputClassName?: string
  label?: string
}

export default function EditableField({
  value,
  onSave,
  multiline = false,
  placeholder = 'Click to edit',
  className = '',
  inputClassName = '',
  label,
}: Props) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const ref = useRef<HTMLTextAreaElement | HTMLInputElement | null>(null)

  useEffect(() => {
    setDraft(value)
  }, [value])

  useEffect(() => {
    if (editing && ref.current) {
      ref.current.focus()
      if (ref.current instanceof HTMLTextAreaElement) {
        ref.current.style.height = 'auto'
        ref.current.style.height = `${ref.current.scrollHeight}px`
      }
    }
  }, [editing])

  async function commit() {
    if (draft === value) {
      setEditing(false)
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onSave(draft)
      setEditing(false)
    } catch (e: any) {
      setError(e?.message || 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  function cancel() {
    setDraft(value)
    setEditing(false)
    setError(null)
  }

  if (!editing) {
    return (
      <div
        className={`group relative cursor-pointer ${className}`}
        onClick={() => setEditing(true)}
        title="Click to edit"
      >
        {value ? (
          multiline ? (
            <span className="whitespace-pre-wrap">{value}</span>
          ) : (
            <span>{value}</span>
          )
        ) : (
          <span className="text-gray-500 italic">{placeholder}</span>
        )}
        <span className="opacity-0 group-hover:opacity-100 absolute -top-1 -right-1 text-[10px] bg-blue-600 text-white px-1.5 py-0.5 rounded">
          edit
        </span>
      </div>
    )
  }

  const Tag = multiline ? 'textarea' : 'input'

  return (
    <div className="space-y-1">
      {label && <div className="text-[10px] uppercase tracking-wider text-gray-500">{label}</div>}
      <Tag
        ref={ref as any}
        value={draft}
        onChange={(e: any) => {
          setDraft(e.target.value)
          if (multiline && e.target instanceof HTMLTextAreaElement) {
            e.target.style.height = 'auto'
            e.target.style.height = `${e.target.scrollHeight}px`
          }
        }}
        onKeyDown={(e: any) => {
          if (e.key === 'Escape') cancel()
          if (e.key === 'Enter' && !multiline && !e.shiftKey) {
            e.preventDefault()
            commit()
          }
          if (e.key === 'Enter' && multiline && (e.metaKey || e.ctrlKey)) {
            e.preventDefault()
            commit()
          }
        }}
        className={`w-full bg-gray-800 border border-blue-600 rounded px-2 py-1 text-white outline-none focus:ring-1 focus:ring-blue-500 ${inputClassName}`}
        rows={multiline ? 3 : undefined}
      />
      <div className="flex items-center gap-2 text-xs">
        <button
          onClick={commit}
          disabled={saving}
          className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white px-2 py-0.5 rounded"
        >
          {saving ? 'Saving...' : 'Save'}
        </button>
        <button
          onClick={cancel}
          disabled={saving}
          className="bg-gray-700 hover:bg-gray-600 text-gray-200 px-2 py-0.5 rounded"
        >
          Cancel
        </button>
        <span className="text-[10px] text-gray-500">
          {multiline ? 'Cmd+Enter to save · Esc to cancel' : 'Enter to save · Esc to cancel'}
        </span>
        {error && <span className="text-red-400">{error}</span>}
      </div>
    </div>
  )
}
