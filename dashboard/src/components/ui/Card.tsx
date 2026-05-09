/**
 * Card — surface primitive.
 *
 * Replaces 50+ ad-hoc instances of `bg-gray-900 border border-gray-800
 * rounded-xl p-X` patterns. One source of truth for surface styling.
 */
import { HTMLAttributes, ReactNode, ElementType } from 'react'
import { clsx } from 'clsx'

export interface CardProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  /** Optional title rendered as the card's heading. */
  title?: ReactNode
  /** Description below the title. */
  description?: ReactNode
  /** Right-aligned slot in the header row. */
  actions?: ReactNode
  /** Visual emphasis level. */
  tone?: 'default' | 'muted' | 'success' | 'warning' | 'danger'
  /** Padding scale. */
  padding?: 'none' | 'sm' | 'md' | 'lg'
  /** Element type — defaults to <section>. */
  as?: ElementType
  children?: ReactNode
}

const TONE_CLS: Record<NonNullable<CardProps['tone']>, string> = {
  default: 'bg-surface border-border',
  muted:   'bg-surface-raised border-border',
  success: 'bg-success-bg/60 border-success-border',
  warning: 'bg-warning-bg/60 border-warning-border',
  danger:  'bg-danger-bg/60 border-danger-border',
}

const PADDING_CLS: Record<NonNullable<CardProps['padding']>, string> = {
  none: 'p-0',
  sm: 'p-3',
  md: 'p-4 sm:p-5',
  lg: 'p-6 sm:p-8',
}

export function Card({
  title,
  description,
  actions,
  tone = 'default',
  padding = 'md',
  as: Tag = 'section',
  className,
  children,
  ...props
}: CardProps) {
  const hasHeader = title || description || actions
  return (
    <Tag
      className={clsx(
        'rounded-lg border shadow-sm',
        TONE_CLS[tone],
        className,
      )}
      {...props}
    >
      {hasHeader && (
        <div className={clsx('flex items-start justify-between gap-3 flex-wrap',
          padding !== 'none' ? 'px-4 sm:px-5 pt-4 sm:pt-5 pb-3' : '',
          !children && (PADDING_CLS[padding] === 'p-0' ? '' : PADDING_CLS[padding]),
        )}>
          <div className="min-w-0 flex-1">
            {title && (
              <h2 className="text-sm font-semibold text-fg tracking-tight">
                {title}
              </h2>
            )}
            {description && (
              <p className="text-2xs text-fg-muted mt-0.5 leading-relaxed">
                {description}
              </p>
            )}
          </div>
          {actions && <div className="flex items-center gap-2 shrink-0">{actions}</div>}
        </div>
      )}
      {children !== undefined && (
        <div className={clsx(hasHeader ? 'px-4 sm:px-5 pb-4 sm:pb-5' : PADDING_CLS[padding])}>
          {children}
        </div>
      )}
    </Tag>
  )
}

export default Card
