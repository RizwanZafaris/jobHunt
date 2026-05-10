/**
 * TabStrip — minimal link-based tab control.
 *
 * Built specifically for the /insights page. We use real navigation
 * (?tab=...) so deep links and back-button behaviour work out of the
 * box, and so the shell can stay a server component.
 *
 * Each tab is a Link; the active one shows a bottom border + the accent
 * background. Designed to slot into existing tokens — no new colours.
 */
import Link from 'next/link'
import { clsx } from 'clsx'
import { Icon, IconName } from './Icon'

export interface TabStripItem {
  /** Stable id, also used for ?tab= value. */
  id: string
  label: string
  icon?: IconName
}

export interface TabStripProps {
  items: TabStripItem[]
  /** The currently-selected id. */
  active: string
  /** Base path for the link. The ?tab= query is appended. */
  basePath: string
  ariaLabel?: string
  className?: string
}

export function TabStrip({ items, active, basePath, ariaLabel = 'Sections', className }: TabStripProps) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={clsx('flex items-center gap-0.5 border-b border-border', className)}
    >
      {items.map((item) => {
        const isActive = item.id === active
        const href = item.id ? `${basePath}?tab=${item.id}` : basePath
        return (
          <Link
            key={item.id}
            href={href}
            role="tab"
            aria-selected={isActive}
            aria-current={isActive ? 'page' : undefined}
            className={clsx(
              'inline-flex items-center gap-1.5 px-3.5 py-2.5 text-2xs font-medium -mb-px border-b-2 transition-colors',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              isActive
                ? 'border-accent text-fg'
                : 'border-transparent text-fg-muted hover:text-fg hover:border-border-strong',
            )}
          >
            {item.icon && <Icon name={item.icon} size={13} />}
            {item.label}
          </Link>
        )
      })}
    </div>
  )
}

export default TabStrip
