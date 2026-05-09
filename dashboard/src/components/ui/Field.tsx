/**
 * Field primitives — accessible inputs with proper labels.
 *
 * Every input gets a real <label htmlFor>. Use these instead of the
 * scattered ad-hoc <input class="bg-gray-800..."> patterns.
 */
'use client'

import { InputHTMLAttributes, SelectHTMLAttributes, TextareaHTMLAttributes, useId, ReactNode } from 'react'
import { clsx } from 'clsx'

const BASE =
  'w-full bg-surface text-fg placeholder:text-fg-subtle border border-border-strong rounded-md px-3 py-2 text-xs ' +
  'focus-visible:outline-none focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30 ' +
  'disabled:opacity-50 transition-colors'

interface BaseFieldProps {
  label: ReactNode
  hint?: ReactNode
  /** Hide the label visually but keep it for AT. */
  srLabel?: boolean
  error?: ReactNode
}

export interface TextInputProps extends BaseFieldProps, Omit<InputHTMLAttributes<HTMLInputElement>, 'size'> {}

export function TextInput({ label, hint, srLabel, error, className, id: idProp, ...props }: TextInputProps) {
  const reactId = useId()
  const id = idProp || reactId
  const hintId = hint ? `${id}-hint` : undefined
  const errId = error ? `${id}-err` : undefined
  return (
    <div className="space-y-1">
      <label
        htmlFor={id}
        className={clsx(
          'block text-2xs font-medium text-fg-muted uppercase tracking-wider',
          srLabel && 'sr-only',
        )}
      >
        {label}
      </label>
      <input
        id={id}
        aria-describedby={[hintId, errId].filter(Boolean).join(' ') || undefined}
        aria-invalid={error ? true : undefined}
        className={clsx(BASE, error && 'border-danger', className)}
        {...props}
      />
      {hint && <p id={hintId} className="text-2xs text-fg-subtle">{hint}</p>}
      {error && <p id={errId} className="text-2xs text-danger" role="alert">{error}</p>}
    </div>
  )
}

export interface SelectProps extends BaseFieldProps, Omit<SelectHTMLAttributes<HTMLSelectElement>, 'size'> {
  children: ReactNode
}

export function Select({ label, hint, srLabel, error, className, id: idProp, children, ...props }: SelectProps) {
  const reactId = useId()
  const id = idProp || reactId
  const hintId = hint ? `${id}-hint` : undefined
  const errId = error ? `${id}-err` : undefined
  return (
    <div className="space-y-1">
      <label
        htmlFor={id}
        className={clsx(
          'block text-2xs font-medium text-fg-muted uppercase tracking-wider',
          srLabel && 'sr-only',
        )}
      >
        {label}
      </label>
      <select
        id={id}
        aria-describedby={[hintId, errId].filter(Boolean).join(' ') || undefined}
        aria-invalid={error ? true : undefined}
        className={clsx(BASE, 'pr-8', error && 'border-danger', className)}
        {...props}
      >
        {children}
      </select>
      {hint && <p id={hintId} className="text-2xs text-fg-subtle">{hint}</p>}
      {error && <p id={errId} className="text-2xs text-danger" role="alert">{error}</p>}
    </div>
  )
}

export interface TextareaProps extends BaseFieldProps, TextareaHTMLAttributes<HTMLTextAreaElement> {}

export function Textarea({ label, hint, srLabel, error, className, id: idProp, ...props }: TextareaProps) {
  const reactId = useId()
  const id = idProp || reactId
  const hintId = hint ? `${id}-hint` : undefined
  const errId = error ? `${id}-err` : undefined
  return (
    <div className="space-y-1">
      <label
        htmlFor={id}
        className={clsx(
          'block text-2xs font-medium text-fg-muted uppercase tracking-wider',
          srLabel && 'sr-only',
        )}
      >
        {label}
      </label>
      <textarea
        id={id}
        aria-describedby={[hintId, errId].filter(Boolean).join(' ') || undefined}
        aria-invalid={error ? true : undefined}
        className={clsx(BASE, 'resize-y', error && 'border-danger', className)}
        {...props}
      />
      {hint && <p id={hintId} className="text-2xs text-fg-subtle">{hint}</p>}
      {error && <p id={errId} className="text-2xs text-danger" role="alert">{error}</p>}
    </div>
  )
}
