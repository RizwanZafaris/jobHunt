/**
 * Pill — small status/quality marker.
 *
 * Replaces inline badge spans scattered across the app. Tone driven by
 * semantic tokens so it theme-shifts. Optional `as="button"` form is a
 * real <button> with aria-pressed (used by OutcomeLogger Yes/No/?).
 */
'use client'

import { ButtonHTMLAttributes, HTMLAttributes, ReactNode } from 'react'
import { clsx } from 'clsx'

export type PillTone = 'neutral' | 'success' | 'warning' | 'danger' | 'info' | 'accent'

const TONE_CLS: Record<PillTone, string> = {
  neutral: 'bg-surface-raised text-fg-muted border-border',
  success: 'bg-success-bg text-success border-success-border',
  warning: 'bg-warning-bg text-warning border-warning-border',
  danger:  'bg-danger-bg text-danger border-danger-border',
  info:    'bg-info-bg text-info border-info-border',
  accent:  'bg-accent text-accent-fg border-accent',
}

const TONE_ACTIVE_CLS: Record<PillTone, string> = {
  neutral: 'bg-fg/10 text-fg border-fg/30',
  success: 'bg-success text-fg-inverse border-success',
  warning: 'bg-warning text-fg-inverse border-warning',
  danger:  'bg-danger text-fg-inverse border-danger',
  info:    'bg-info text-fg-inverse border-info',
  accent:  'bg-accent text-accent-fg border-accent',
}

interface PillBaseProps {
  tone?: PillTone
  /** When this is a toggleable selection, set this to indicate state. */
  pressed?: boolean
  size?: 'xs' | 'sm'
  icon?: ReactNode
  children: ReactNode
  className?: string
}

export type PillSpanProps = PillBaseProps &
  Omit<HTMLAttributes<HTMLSpanElement>, keyof PillBaseProps | 'children'> & {
    as?: 'span'
  }

export type PillButtonProps = PillBaseProps &
  Omit<ButtonHTMLAttributes<HTMLButtonElement>, keyof PillBaseProps | 'children'> & {
    as: 'button'
  }

export type PillProps = PillSpanProps | PillButtonProps

function pillClass(tone: PillTone, pressed: boolean | undefined, size: 'xs' | 'sm', interactive: boolean, extra?: string) {
  return clsx(
    'inline-flex items-center gap-1 border rounded-md font-medium transition-colors',
    size === 'xs' ? 'text-2xs px-2 py-0.5' : 'text-xs px-2.5 py-1',
    pressed ? TONE_ACTIVE_CLS[tone] : TONE_CLS[tone],
    interactive && 'cursor-pointer focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent hover:bg-surface',
    extra,
  )
}

export function Pill(props: PillProps) {
  const { tone = 'neutral', pressed, size = 'xs', icon, className, children } = props
  if (props.as === 'button') {
    const { as, tone: _t, pressed: _p, size: _s, icon: _i, className: _c, children: _ch, ...rest } = props
    return (
      <button
        type="button"
        aria-pressed={pressed}
        className={pillClass(tone, pressed, size, true, className)}
        {...rest}
      >
        {icon}
        {children}
      </button>
    )
  }
  const { as, tone: _t, pressed: _p, size: _s, icon: _i, className: _c, children: _ch, ...rest } = props
  return (
    <span className={pillClass(tone, pressed, size, false, className)} {...rest}>
      {icon}
      {children}
    </span>
  )
}

export default Pill
