'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'

const TABS = [
  { href: '/', label: 'Pipeline' },
  { href: '/profile', label: 'Profile' },
  { href: '/profile/keywords', label: 'Keywords' },
  { href: '/profile/sources', label: 'Sources' },
]

export default function ProfileNav() {
  const path = usePathname()
  return (
    <nav className="flex items-center gap-1 text-xs">
      {TABS.map((t) => {
        const active = path === t.href
        return (
          <Link
            key={t.href}
            href={t.href}
            className={`px-3 py-1.5 rounded-lg font-medium transition-colors ${
              active
                ? 'bg-blue-600 text-white'
                : 'text-gray-300 hover:text-white hover:bg-gray-800'
            }`}
          >
            {t.label}
          </Link>
        )
      })}
    </nav>
  )
}
