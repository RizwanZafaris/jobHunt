/**
 * UserMenu — top-right avatar + dropdown.
 *
 * Replaces the /profile primary nav tab. Click the avatar to reveal a
 * compact menu (Profile, Settings, Sign out). Built minimal — we
 * deliberately don't pull in Radix here; existing tokens + a focus trap
 * are enough for a 3-item menu.
 *
 * a11y:
 *   - real <button> trigger with aria-haspopup="menu" + aria-expanded
 *   - menu items are <a> for routes and <button> for actions
 *   - Esc closes; click-outside closes; first item gets focus on open
 *
 * TODO: wire `email` to real session — see _pending_endpoints.md (/auth/me)
 */
'use client'

import Link from 'next/link'
import { useEffect, useRef, useState } from 'react'
import { clsx } from 'clsx'
import { Icon } from './Icon'

export interface UserMenuProps {
  /** User email — shown as the menu header. */
  email?: string
}

function initialsFor(email?: string): string {
  if (!email) return '?'
  const local = email.split('@')[0] || ''
  const parts = local.split(/[._-]+/).filter(Boolean)
  if (parts.length >= 2) {
    return `${parts[0][0] ?? ''}${parts[1][0] ?? ''}`.toUpperCase()
  }
  return (local.slice(0, 2) || '?').toUpperCase()
}

export function UserMenu({ email = 'rizwanzaffar.pk@gmail.com' }: UserMenuProps) {
  const [open, setOpen] = useState(false)
  const containerRef = useRef<HTMLDivElement | null>(null)
  const firstItemRef = useRef<HTMLAnchorElement | null>(null)

  // Click outside / Escape to close
  useEffect(() => {
    if (!open) return
    function onPointer(e: PointerEvent) {
      if (!containerRef.current) return
      if (!containerRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') {
        setOpen(false)
      }
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  // Focus first item on open for keyboard users
  useEffect(() => {
    if (open) {
      // Defer to allow render
      const id = window.setTimeout(() => firstItemRef.current?.focus(), 0)
      return () => window.clearTimeout(id)
    }
  }, [open])

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={`User menu for ${email}`}
        title={email}
        className={clsx(
          'inline-flex items-center justify-center w-9 h-9 rounded-full text-2xs font-semibold',
          'bg-surface-raised text-fg border border-border hover:border-border-strong',
          'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors',
        )}
      >
        <span aria-hidden>{initialsFor(email)}</span>
      </button>

      {open && (
        <div
          role="menu"
          aria-label="User menu"
          className={clsx(
            'absolute right-0 mt-2 w-60 rounded-lg border border-border bg-surface-overlay shadow-lg z-50 overflow-hidden',
          )}
        >
          <div className="px-3 py-2.5 border-b border-border">
            <p className="text-2xs uppercase tracking-wider text-fg-subtle font-semibold">
              Signed in
            </p>
            <p className="text-xs text-fg font-medium truncate" title={email}>
              {email}
            </p>
          </div>
          <div className="py-1">
            <Link
              ref={firstItemRef}
              role="menuitem"
              href="/profile"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 px-3 py-2 text-xs text-fg hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <Icon name="document" size={14} />
              Profile
            </Link>
            <Link
              role="menuitem"
              href="/profile/keywords"
              onClick={() => setOpen(false)}
              className="flex items-center gap-2.5 px-3 py-2 text-xs text-fg hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <Icon name="sliders" size={14} />
              Settings
            </Link>
            <button
              type="button"
              role="menuitem"
              onClick={() => {
                setOpen(false)
                // TODO: wire to /auth/sign-out
                if (typeof window !== 'undefined') {
                  window.alert('Sign out not yet wired — see _pending_endpoints.md')
                }
              }}
              className="w-full text-left flex items-center gap-2.5 px-3 py-2 text-xs text-fg hover:bg-surface-raised focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
            >
              <Icon name="x" size={14} />
              Sign out
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default UserMenu
