/**
 * PageHeader — replaces the 10× duplicated gradient hero panel.
 *
 * Quiet, typographically-led header. No gradient, no glassmorphism.
 * Uses display serif for the page title (distinctive vs. typical
 * sans-everything AI dashboards).
 */
import { ReactNode } from 'react'
import { clsx } from 'clsx'

export interface PageHeaderProps {
  eyebrow?: ReactNode
  title: ReactNode
  description?: ReactNode
  actions?: ReactNode
  className?: string
}

export function PageHeader({ eyebrow, title, description, actions, className }: PageHeaderProps) {
  return (
    <header className={clsx('flex items-start justify-between gap-4 flex-wrap pb-2', className)}>
      <div className="min-w-0 flex-1">
        {eyebrow && (
          <p className="text-2xs font-medium uppercase tracking-[0.12em] text-fg-subtle mb-1.5">
            {eyebrow}
          </p>
        )}
        <h1 className="text-2xl sm:text-3xl font-display font-normal text-fg leading-tight tracking-tight">
          {title}
        </h1>
        {description && (
          <p className="text-sm text-fg-muted mt-2 max-w-2xl leading-relaxed">
            {description}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0 flex-wrap">{actions}</div>}
    </header>
  )
}

export default PageHeader
