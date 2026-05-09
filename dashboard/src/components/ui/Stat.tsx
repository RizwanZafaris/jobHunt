/**
 * Stat — single key-metric block.
 *
 * Replaces the AI-tile "5-up colorful grid" pattern with a quieter,
 * typographically-led list. No emoji icons, no per-tile color
 * sledgehammer — the number is the signal.
 */
import { ReactNode } from 'react'
import { clsx } from 'clsx'
import { Icon, IconName } from './Icon'

export interface StatProps {
  label: ReactNode
  value: ReactNode
  /** Smaller text below the value, e.g. "3 new today". */
  hint?: ReactNode
  /** Optional small trailing icon (subtle, monochrome). */
  icon?: IconName
  /** Single semantic tint reserved for status, not decoration. */
  tone?: 'default' | 'success' | 'warning' | 'danger'
  /** Defaults to <div>; pass 'a' or 'button' if interactive. */
  className?: string
}

const TONE_VALUE: Record<NonNullable<StatProps['tone']>, string> = {
  default: 'text-fg',
  success: 'text-success',
  warning: 'text-warning',
  danger:  'text-danger',
}

export function Stat({ label, value, hint, icon, tone = 'default', className }: StatProps) {
  return (
    <div className={clsx('flex flex-col gap-1', className)}>
      <div className="flex items-center gap-1.5 text-2xs uppercase tracking-[0.08em] text-fg-subtle font-medium">
        {icon && <Icon name={icon} size={12} />}
        {label}
      </div>
      <div className={clsx('text-2xl font-semibold tnum leading-none', TONE_VALUE[tone])}>
        {value}
      </div>
      {hint && <div className="text-2xs text-fg-muted">{hint}</div>}
    </div>
  )
}

export default Stat
