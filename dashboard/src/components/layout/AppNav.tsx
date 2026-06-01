/**
 * AppNav — primary site navigation.
 *
 * Collapsed from 7 tabs (engineer-shaped) down to 5 (job-seeker-shaped):
 *   - Today        — the ranked "what to do now" surface
 *   - Targets      — the company / job pipeline
 *   - Applications — outbound + outcome tracking
 *   - Network      — referral graph (Sprint 2 placeholder)
 *   - Insights     — Personas + Costs + System(Boss), tabbed
 *
 * Profile lives in a top-right user menu (avatar dropdown), rendered by
 * `UserMenu` from AppShell — not here. The legacy /profile sub-nav is
 * still rendered via `ProfileSubNav` when the user is on a profile route.
 */
'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { clsx } from 'clsx'
import { Icon, IconName } from '@/components/ui/Icon'

interface NavItem {
  href: string
  label: string
  icon: IconName
}

// 2026-05-12: added /linkedin to the primary nav. The G4 LinkedIn engine
// produces drafts that surface on /today only when there's an approved
// scheduled-for-today draft. Without a top-level entry point, users
// couldn't reach the LinkedIn content calendar to approve / schedule
// drafts in the first place — circular dead-zone.
// BUG-023: the nav entry labeled "Targets" now points straight at the
// /companies route (where its content has always lived). The old /targets
// page was a server-side redirect and the round-trip silently changed the
// URL — bookmarks landed somewhere unexpected and the browser tab title
// said "Companies". Route file has been deleted.
const PRIMARY: NavItem[] = [
  { href: '/today',        label: 'Today',        icon: 'sun' },
  { href: '/jobs/rate',    label: 'Rate a Job',   icon: 'search' },
  { href: '/companies',    label: 'Targets',      icon: 'target' },
  { href: '/applications', label: 'Applications', icon: 'clipboard-list' },
  { href: '/linkedin',     label: 'LinkedIn',     icon: 'sparkles' },
  { href: '/network',      label: 'Network',      icon: 'users' },
  { href: '/insights',     label: 'Insights',     icon: 'bar-chart-3' },
]

const PROFILE_SUB: NavItem[] = [
  { href: '/profile',                 label: 'Master',          icon: 'document' },
  { href: '/profile/keywords',        label: 'Keywords',        icon: 'tag' },
  { href: '/profile/recommendations', label: 'Recommendations', icon: 'sparkles' },
  { href: '/profile/sources',         label: 'Sources',         icon: 'link' },
]

function isActive(href: string, pathname: string): boolean {
  if (href === '/today') return pathname === '/today' || pathname === '/'
  if (href === '/insights') {
    return (
      pathname === href ||
      pathname.startsWith(href + '/') ||
      pathname === '/personas' ||
      pathname.startsWith('/personas/') ||
      pathname === '/costs' ||
      pathname.startsWith('/costs/') ||
      pathname === '/boss' ||
      pathname.startsWith('/boss/')
    )
  }
  // BUG-023: keep the active highlight on every /companies/* page so
  // detail views still show the parent nav entry as current.
  if (href === '/companies') {
    return pathname === href || pathname.startsWith(href + '/')
  }
  return pathname === href || pathname.startsWith(href + '/')
}

function isProfileSection(pathname: string) {
  return pathname === '/profile' || pathname.startsWith('/profile/')
}

export function AppNav() {
  const pathname = usePathname()
  const [open, setOpen] = useState(false)

  // Close drawer on route change
  useEffect(() => {
    setOpen(false)
  }, [pathname])

  // Lock body scroll when drawer open
  useEffect(() => {
    if (open) {
      document.body.style.overflow = 'hidden'
      return () => {
        document.body.style.overflow = ''
      }
    }
  }, [open])

  return (
    <>
      {/* Desktop nav */}
      <nav aria-label="Primary" className="hidden md:flex items-center gap-0.5">
        {PRIMARY.map((item) => {
          const active = isActive(item.href, pathname)
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={active ? 'page' : undefined}
              className={clsx(
                'inline-flex items-center gap-1.5 px-3 py-2 rounded-md text-2xs font-medium transition-colors',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                active
                  ? 'bg-accent text-accent-fg'
                  : 'text-fg-muted hover:text-fg hover:bg-surface-raised',
              )}
            >
              <Icon name={item.icon} size={14} />
              {item.label}
            </Link>
          )
        })}
      </nav>

      {/* Mobile menu trigger */}
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-label="Open navigation menu"
        aria-expanded={open}
        aria-controls="mobile-nav"
        className="md:hidden inline-flex items-center justify-center w-9 h-9 rounded-md text-fg-muted hover:text-fg hover:bg-surface-raised border border-transparent hover:border-border focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      >
        <Icon name="menu" size={18} />
      </button>

      {/* Mobile drawer */}
      {open && (
        <div className="md:hidden fixed inset-0 z-50">
          <button
            type="button"
            aria-label="Close navigation menu"
            onClick={() => setOpen(false)}
            className="absolute inset-0 bg-fg/40"
          />
          <nav
            id="mobile-nav"
            aria-label="Primary"
            className="absolute right-0 top-0 bottom-0 w-72 max-w-[85vw] bg-surface border-l border-border shadow-lg p-4 flex flex-col gap-1 overflow-y-auto"
          >
            <div className="flex items-center justify-between mb-3">
              <span className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
                Navigate
              </span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                aria-label="Close menu"
                className="inline-flex items-center justify-center w-9 h-9 rounded-md text-fg-muted hover:text-fg hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
              >
                <Icon name="x" size={18} />
              </button>
            </div>
            {PRIMARY.map((item) => {
              const active = isActive(item.href, pathname)
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? 'page' : undefined}
                  className={clsx(
                    'inline-flex items-center gap-2 px-3 py-3 rounded-md text-sm font-medium min-h-11 transition-colors',
                    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                    active
                      ? 'bg-accent text-accent-fg'
                      : 'text-fg hover:bg-surface-raised',
                  )}
                >
                  <Icon name={item.icon} size={16} />
                  {item.label}
                </Link>
              )
            })}
            {isProfileSection(pathname) && (
              <div className="mt-3 pt-3 border-t border-border">
                <span className="px-3 text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
                  Profile sections
                </span>
                <div className="mt-1 flex flex-col gap-0.5">
                  {PROFILE_SUB.map((item) => {
                    const active = pathname === item.href
                    return (
                      <Link
                        key={item.href}
                        href={item.href}
                        aria-current={active ? 'page' : undefined}
                        className={clsx(
                          'inline-flex items-center gap-2 px-3 py-2.5 rounded-md text-xs font-medium min-h-11',
                          active ? 'bg-surface-raised text-fg' : 'text-fg-muted hover:text-fg hover:bg-surface-raised',
                        )}
                      >
                        <Icon name={item.icon} size={14} />
                        {item.label}
                      </Link>
                    )
                  })}
                </div>
              </div>
            )}
          </nav>
        </div>
      )}
    </>
  )
}

/**
 * ProfileSubNav — desktop sub-navigation for /profile/* sections.
 * Hidden on mobile (drawer shows it instead).
 */
export function ProfileSubNav() {
  const pathname = usePathname()
  if (!isProfileSection(pathname)) return null
  return (
    <nav aria-label="Profile sections" className="hidden md:flex items-center gap-1 border-b border-border -mx-4 sm:-mx-6 lg:-mx-8 px-4 sm:px-6 lg:px-8 pb-2">
      {PROFILE_SUB.map((item) => {
        const active = pathname === item.href
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? 'page' : undefined}
            className={clsx(
              'inline-flex items-center gap-1.5 px-3 py-2 text-2xs font-medium rounded-md transition-colors',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              active ? 'text-fg bg-surface-raised' : 'text-fg-subtle hover:text-fg hover:bg-surface-raised',
            )}
          >
            <Icon name={item.icon} size={12} />
            {item.label}
          </Link>
        )
      })}
    </nav>
  )
}

export default AppNav
