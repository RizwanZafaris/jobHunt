/**
 * EmptyState — single empty pattern across the app.
 *
 * Replaces six divergent empty UIs (some with seed instructions, some bare).
 */
import { ReactNode } from 'react'
import { clsx } from 'clsx'
import { Icon, IconName } from './Icon'

export interface EmptyStateProps {
  icon?: IconName
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  hint?: ReactNode
  tone?: 'default' | 'warning'
  className?: string
}

export function EmptyState({ icon, title, description, action, hint, tone = 'default', className }: EmptyStateProps) {
  return (
    <div
      className={clsx(
        'rounded-lg border-2 border-dashed text-center p-8',
        tone === 'warning' ? 'border-warning-border bg-warning-bg/40' : 'border-border bg-surface',
        className,
      )}
    >
      {icon && (
        <div className="inline-flex items-center justify-center w-10 h-10 rounded-full bg-surface-raised text-fg-muted mb-3">
          <Icon name={icon} size={18} />
        </div>
      )}
      <h2 className="text-base font-semibold text-fg">{title}</h2>
      {description && (
        <p className="text-xs text-fg-muted mt-1.5 max-w-md mx-auto leading-relaxed">{description}</p>
      )}
      {action && <div className="mt-4 flex justify-center">{action}</div>}
      {hint && <div className="mt-3 text-2xs text-fg-subtle">{hint}</div>}
    </div>
  )
}

export default EmptyState
