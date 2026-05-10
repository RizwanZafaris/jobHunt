/**
 * AppShell — shared layout chrome for every dashboard page.
 *
 * Replaces the 12 inline `<header><div className="bg-gray-900..."><h1>🤖 Job Hunt AI</h1>...`
 * copies. Single source of truth for header, max-width, padding, ProfileSubNav.
 *
 * Pass `wide` to opt into the wider variant (Pipeline/Applications); default
 * is the standard reading-width container used everywhere else.
 */
import { ReactNode } from 'react'
import Link from 'next/link'
import { clsx } from 'clsx'
import { AppNav, ProfileSubNav } from './AppNav'
import { ThemeToggle } from '@/components/ui/ThemeToggle'
import { UserMenu } from '@/components/ui/UserMenu'

export interface AppShellProps {
  children: ReactNode
  /** Right-side action slot in the top bar (e.g. Pipeline run buttons). */
  actions?: ReactNode
  /** Wider layout for tabular pages. Default `false` (max-w-6xl). */
  wide?: boolean
}

export function AppShell({ children, actions, wide = false }: AppShellProps) {
  const containerCls = clsx(
    'mx-auto px-4 sm:px-6 lg:px-8',
    wide ? 'max-w-7xl' : 'max-w-6xl',
  )
  return (
    <div className="min-h-screen bg-bg text-fg">
      <header className="border-b border-border bg-bg sticky top-0 z-30">
        <div className={clsx(containerCls, 'h-14 flex items-center justify-between gap-4')}>
          <div className="flex items-center gap-6 min-w-0">
            <Link
              href="/today"
              className="font-display text-lg tracking-tight text-fg hover:text-fg-muted transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent rounded"
            >
              Job Hunt
            </Link>
            <AppNav />
          </div>
          <div className="flex items-center gap-1.5">
            {actions}
            <ThemeToggle />
            <UserMenu />
          </div>
        </div>
      </header>

      <main id="main" tabIndex={-1} className={clsx(containerCls, 'py-6 sm:py-8 space-y-6 outline-none')}>
        <ProfileSubNav />
        {children}
      </main>
    </div>
  )
}

export default AppShell
