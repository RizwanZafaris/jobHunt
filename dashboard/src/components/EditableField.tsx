'use client'

/**
 * EditableField — accessible inline-edit primitive.
 *
 * Fixes the audit's P0 a11y blocker: the trigger is now a real <button>
 * with keyboard activation (Space/Enter), focus-visible affordance, and
 * an always-visible edit hint on touch devices.
 *
 * Save status is announced via an aria-live region so screen-reader
 * users hear the result of their edit.
 */
import { useEffect, useId, useRef, useState } from 'react'
import { clsx } from 'clsx'
import { Icon } from '@/components/ui/Icon'

interface Props {
  value: string
  onSave: (next: string) => Promise<void>
  multiline?: boolean
  placeholder?: string
  className?: string
  inputClassName?: string
  label?: string
  /** Hidden label for screen readers when no visible label is provided. */
  srLabel?: string
}

export default function EditableField({
  value,
  onSave,
  multiline = false,
  placeholder = 'Click to edit',
  className = '',
  inputClassName = '',
  label,
  srLabel,
}: Props) {
  const id = useId()
  const editId = `${id}-input`
  const statusId = `${id}-status`

  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(value)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<string>('')
  const inputRef = useRef<HTMLTextAreaElement | HTMLInputElement | null>(null)
  const triggerRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    setDraft(value)
  }, [value])

  useEffect(() => {
    if (editing && inputRef.current) {
      inputRef.current.focus()
      if (inputRef.current instanceof HTMLTextAreaElement) {
        inputRef.current.style.height = 'auto'
        inputRef.current.style.height = `${inputRef.current.scrollHeight}px`
      }
    } else if (!editing && triggerRef.current) {
      // Return focus to the trigger when commit/cancel finishes
      triggerRef.current.focus()
    }
  }, [editing])

  async function commit() {
    if (draft === value) {
      setEditing(false)
      setStatus('No changes')
      return
    }
    setSaving(true)
    setError(null)
    try {
      await onSave(draft)
      setStatus('Saved')
      setEditing(false)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : 'Save failed'
      setError(msg)
      setStatus('Save failed')
    } finally {
      setSaving(false)
    }
  }

  function cancel() {
    setDraft(value)
    setEditing(false)
    setError(null)
    setStatus('Edit cancelled')
  }

  if (!editing) {
    return (
      <div className={clsx('group relative inline-block max-w-full', className)}>
        <button
          ref={triggerRef}
          type="button"
          onClick={() => setEditing(true)}
          aria-label={
            srLabel
              ? `Edit ${srLabel}`
              : value
              ? `Edit "${value.length > 60 ? value.slice(0, 60) + '…' : value}"`
              : `Edit ${placeholder}`
          }
          className={clsx(
            'block text-left max-w-full rounded -mx-1 px-1 -my-0.5 py-0.5',
            'cursor-text border border-transparent hover:border-border-strong hover:bg-surface-raised/40',
            'focus-visible:outline-none focus-visible:border-accent focus-visible:bg-surface-raised',
            'transition-colors',
          )}
        >
          {value ? (
            multiline ? (
              <span className="whitespace-pre-wrap">{value}</span>
            ) : (
              <span>{value}</span>
            )
          ) : (
            <span className="text-fg-subtle italic">{placeholder}</span>
          )}
        </button>
        <span
          aria-hidden="true"
          className="opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 absolute -top-2 -right-1 inline-flex items-center gap-0.5 text-2xs bg-accent text-accent-fg px-1.5 py-0.5 rounded font-medium pointer-events-none transition-opacity"
        >
          <Icon name="pencil" size={9} />
          edit
        </span>
        <span id={statusId} role="status" aria-live="polite" className="sr-only">
          {status}
        </span>
      </div>
    )
  }

  const Tag = multiline ? 'textarea' : 'input'

  return (
    <div className="space-y-1">
      {label && (
        <label htmlFor={editId} className="block text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
          {label}
        </label>
      )}
      {!label && srLabel && (
        <label htmlFor={editId} className="sr-only">{srLabel}</label>
      )}
      <Tag
        id={editId}
        ref={inputRef as React.MutableRefObject<HTMLInputElement & HTMLTextAreaElement>}
        value={draft}
        aria-describedby={statusId}
        aria-invalid={error ? true : undefined}
        onChange={(e) => {
          const v = (e.target as HTMLInputElement | HTMLTextAreaElement).value
          setDraft(v)
          if (multiline && e.target instanceof HTMLTextAreaElement) {
            e.target.style.height = 'auto'
            e.target.style.height = `${e.target.scrollHeight}px`
          }
        }}
        onKeyDown={(e) => {
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
        className={clsx(
          'w-full bg-surface text-fg border border-accent rounded-md px-2 py-1.5 text-sm',
          'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent/30',
          inputClassName,
        )}
        rows={multiline ? 3 : undefined}
      />
      <div className="flex items-center gap-2 text-2xs">
        <button
          type="button"
          onClick={commit}
          disabled={saving}
          className="bg-accent text-accent-fg hover:bg-accent-hover disabled:opacity-50 px-3 py-1.5 rounded-md font-medium min-h-9 inline-flex items-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          onClick={cancel}
          disabled={saving}
          className="bg-surface-raised border border-border-strong text-fg hover:bg-surface px-3 py-1.5 rounded-md font-medium min-h-9 inline-flex items-center focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          Cancel
        </button>
        <span className="text-fg-subtle">
          {multiline ? 'Cmd/Ctrl+Enter to save · Esc to cancel' : 'Enter to save · Esc to cancel'}
        </span>
        {error && <span className="text-danger" role="alert">{error}</span>}
      </div>
      <span id={statusId} role="status" aria-live="polite" className="sr-only">
        {status}
      </span>
    </div>
  )
}
