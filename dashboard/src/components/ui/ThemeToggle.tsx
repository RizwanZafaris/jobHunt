'use client'

import { useEffect, useState } from 'react'
import { Icon } from './Icon'

type Theme = 'light' | 'dark'

/** Persist theme to both localStorage (client preference) and a cookie
 *  (so SSR can read it on the next request). */
function persistTheme(next: Theme) {
  try {
    localStorage.setItem('theme', next)
  } catch {}
  // 1-year max-age, root path, lax for normal navigations
  document.cookie = `theme=${next}; path=/; max-age=31536000; samesite=lax`
}

export function ThemeToggle({ className = '' }: { className?: string }) {
  const [theme, setTheme] = useState<Theme>('dark')
  const [mounted, setMounted] = useState(false)

  useEffect(() => {
    const current = (document.documentElement.getAttribute('data-theme') as Theme | null) || 'dark'
    setTheme(current)
    setMounted(true)
  }, [])

  function toggle() {
    const next: Theme = theme === 'dark' ? 'light' : 'dark'
    document.documentElement.setAttribute('data-theme', next)
    persistTheme(next)
    setTheme(next)
  }

  if (!mounted) {
    // Render a static placeholder on the server / before hydration.
    // Hidden from AT to avoid duplicate announcements.
    return (
      <button
        type="button"
        aria-hidden="true"
        tabIndex={-1}
        className={`inline-flex items-center justify-center w-9 h-9 rounded-md text-fg-muted ${className}`}
      >
        <Icon name="moon" size={16} />
      </button>
    )
  }

  return (
    <button
      type="button"
      onClick={toggle}
      aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
      title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}
      className={`inline-flex items-center justify-center w-9 h-9 rounded-md text-fg-muted hover:text-fg hover:bg-surface-raised border border-transparent hover:border-border focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent transition-colors ${className}`}
    >
      <Icon name={theme === 'dark' ? 'sun' : 'moon'} size={16} />
    </button>
  )
}

export default ThemeToggle
