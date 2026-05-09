/**
 * Skeleton — loading shimmer.
 *
 * Replaces "Loading…" plain-text loaders. Honors prefers-reduced-motion
 * via a static fallback inside @media (handled in globals.css).
 */
import { HTMLAttributes } from 'react'
import { clsx } from 'clsx'

export function Skeleton({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      role="status"
      aria-label="Loading"
      className={clsx(
        'animate-pulse rounded bg-surface-raised',
        className,
      )}
      {...props}
    />
  )
}

export default Skeleton
