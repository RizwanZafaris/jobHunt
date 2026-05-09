/**
 * LiveRegion — polite/assertive aria-live announcement.
 *
 * Wraps async-status messages so screen-reader users hear when saves
 * succeed or fail.
 */
import { HTMLAttributes } from 'react'
import { clsx } from 'clsx'

export interface LiveRegionProps extends HTMLAttributes<HTMLDivElement> {
  level?: 'polite' | 'assertive'
  visible?: boolean
}

export function LiveRegion({
  level = 'polite',
  visible = true,
  className,
  children,
  ...props
}: LiveRegionProps) {
  return (
    <div
      role="status"
      aria-live={level}
      aria-atomic="true"
      className={clsx(visible ? '' : 'sr-only', className)}
      {...props}
    >
      {children}
    </div>
  )
}

export default LiveRegion
