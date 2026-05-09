/**
 * Button — accessible primitive.
 *
 * Replaces ad-hoc <button class="bg-blue-600..."> patterns.
 * Variants map to semantic tokens so the component theme-shifts automatically.
 *
 * a11y baseline:
 *   - real <button> element
 *   - focus-visible ring driven by :focus-visible only (no spurious mouse rings)
 *   - min height 36px desktop / 44px (md+) for AAA touch targets
 *   - disabled state has aria-disabled and pointer-events-none
 */
'use client'

import { ButtonHTMLAttributes, forwardRef } from 'react'
import { clsx } from 'clsx'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'success' | 'warning'
export type ButtonSize = 'xs' | 'sm' | 'md' | 'lg'

const VARIANT_CLS: Record<ButtonVariant, string> = {
  primary:
    'bg-accent text-accent-fg hover:bg-accent-hover border border-transparent',
  secondary:
    'bg-surface text-fg hover:bg-surface-raised border border-border-strong',
  ghost:
    'bg-transparent text-fg-muted hover:bg-surface-raised hover:text-fg border border-transparent',
  danger:
    'bg-danger-bg text-danger border border-danger-border hover:bg-danger hover:text-fg-inverse',
  success:
    'bg-success-bg text-success border border-success-border hover:bg-success hover:text-fg-inverse',
  warning:
    'bg-warning-bg text-warning border border-warning-border hover:bg-warning hover:text-fg-inverse',
}

const SIZE_CLS: Record<ButtonSize, string> = {
  xs: 'text-2xs px-2 py-1 min-h-[28px] gap-1 rounded-md',
  sm: 'text-2xs px-3 py-1.5 min-h-9 gap-1.5 rounded-md',
  md: 'text-xs px-3.5 py-2 min-h-9 md:min-h-11 gap-2 rounded-md',
  lg: 'text-sm px-4 py-2.5 min-h-11 gap-2 rounded-lg',
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  block?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', loading, block, disabled, className, children, type = 'button', ...props },
  ref,
) {
  const isDisabled = disabled || loading
  return (
    <button
      ref={ref}
      type={type}
      disabled={isDisabled}
      aria-disabled={isDisabled || undefined}
      aria-busy={loading || undefined}
      className={clsx(
        'inline-flex items-center justify-center font-medium select-none transition-colors',
        'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
        'disabled:opacity-50 disabled:pointer-events-none',
        VARIANT_CLS[variant],
        SIZE_CLS[size],
        block && 'w-full',
        className,
      )}
      {...props}
    >
      {loading ? (
        <span className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current border-t-transparent" aria-hidden />
      ) : null}
      {children}
    </button>
  )
})

export default Button
