/**
 * AppNav — primary site navigation.
 *
 * Replaces the 9-tab horizontal ProfileNav with:
 *   - 6 top-level tabs (Pipeline, Targets, Applications, Personas, Costs, Profile)
 *   - Profile sub-nav (Master, Keywords, Recommendations, Sources) shown
 *     only when the user is in the /profile section
 *   - mobile drawer below md: breakpoint
 *   - aria-current="page" on the active link
 */
'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'
import { clsx } from 'clsx'
import { Icon } from '@/components/ui/Icon'

interface NavItem {
  href: string
  label: string
}

const PRIMARY: NavItem[] = [
  { href: '/',             label: 'Pipeline' },
  { href: '/companies',    label: 'Targets' },
  { href: '/applications', label: 'Applications' },
  { href: '/personas',     label: 'Personas' },
  { href: '/costs',        label: 'Costs' },
  { href: '/boss',         label: 'Boss' },
  { href: '/profile',      label: 'Profile' },
]

const PROFILE_SUB: NavItem[] = [
  { href: '/profile',                 label: 'Master' },
  { href: '/profile/keywords',        label: 'Keywords' },
  { href: '/profile/recommendations', label: 'Recommendations' },
  { href: '/profile/sources',         label: 'Sources' },
]

function isActive(href: string, pathname: string): boolean {
  if (href === '/') return pathname === '/'
  if (href === '/profile') return pathname === '/profile'
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
          // Highlight Profile when in any /profile/* sub-route
          const isProfile = item.href === '/profile' && isProfileSection(pathname)
          const showActive = active || isProfile
          return (
            <Link
              key={item.href}
              href={item.href}
              aria-current={showActive ? 'page' : undefined}
              className={clsx(
                'px-3 py-2 rounded-md text-2xs font-medium transition-colors',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                showActive
                  ? 'bg-accent text-accent-fg'
                  : 'text-fg-muted hover:text-fg hover:bg-surface-raised',
              )}
            >
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
              const active = isActive(item.href, pathname) || (item.href === '/profile' && isProfileSection(pathname))
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  aria-current={active ? 'page' : undefined}
                  className={clsx(
                    'px-3 py-3 rounded-md text-sm font-medium min-h-11 flex items-center transition-colors',
                    'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
                    active
                      ? 'bg-accent text-accent-fg'
                      : 'text-fg hover:bg-surface-raised',
                  )}
                >
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
                          'px-3 py-2.5 rounded-md text-xs font-medium min-h-11 flex items-center',
                          active ? 'bg-surface-raised text-fg' : 'text-fg-muted hover:text-fg hover:bg-surface-raised',
                        )}
                      >
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
              'px-3 py-2 text-2xs font-medium rounded-md transition-colors',
              'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent',
              active ? 'text-fg bg-surface-raised' : 'text-fg-subtle hover:text-fg hover:bg-surface-raised',
            )}
          >
            {item.label}
          </Link>
        )
      })}
    </nav>
  )
}

export default AppNav
